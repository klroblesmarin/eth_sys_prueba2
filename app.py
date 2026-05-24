import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai
import fitz  # PyMuPDF: Necesario para renderizar PDFs evadiendo el bloqueo del navegador

# Configuración inicial de la página
st.set_page_config(page_title="Simulador BioSTEAM & Tutor IA", layout="wide")

# ===============================================
# 1. LÓGICA DE SIMULACIÓN Y CÁLCULOS
# ===============================================
def run_simulation(params):
    # Limpiar flujos previos para evitar errores de ID duplicado
    bst.main_flowsheet.clear()
    
    # Configuración de Químicos
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # Precios de Servicios
    bst.settings.electricity_price = params['p_luz']
    
    # Corrientes de entrada
    mosto = bst.Stream("1_MOSTO", 
                       Water=43.2, Ethanol=4.9, units="kmol/h",
                       T=params['t_mosto'] + 273.15, P=101325)
    mosto.price = params['p_mosto_in']

    vinazas_retorno = bst.Stream("Vinazas_Retorno", Water=43.335, units="kmol/h",
                                 T=90+273.15, P=300000)

    # Equipos
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno), 
                         outs=("3_MOSTO_PRE", "DRENAJE"), phase0="l", phase1="l")
    W210.outs[0].T = 85 + 273.15

    # Calentador Auxiliar
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla", T=params['t_w220_out'] + 273.15)
    
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bif", P=params['p_v100'] * 101325)
    
    # CORRECCIÓN 1: Parametrización de la presión del Flash para acoplarse a la válvula
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor", "Vinazas"), P=params['p_v100'] * 101325, Q=0)
    
    # Condensador
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto", T=25+273.15)

    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # Definir y correr Sistema
    sys = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    
    # Simulación
    sys.simulate()
    
    # Cálculo de energía
    calor_enfriamiento_kw = 0
    calor_calentamiento_kw = 0
    
    for u in sys.units:
        for hu in u.heat_utilities:
            if hu.duty is not None:
                if hu.duty < 0:
                    calor_enfriamiento_kw += abs(hu.duty)
                else:
                    calor_calentamiento_kw += hu.duty
    
    calor_enfriamiento_kw /= 3600
    calor_calentamiento_kw /= 3600
    
    # Análisis Económico
    prod = sys.flowsheet.stream.Producto
    prod.price = params['p_etanol_vta']
    
    costo_servicios = (calor_enfriamiento_kw * params['p_agua'] / 1000) + (calor_calentamiento_kw * params['p_vapor'] / 1000)
    ventas_por_hora = prod.F_mass * prod.price
    costo_materia_prima = mosto.F_mass * mosto.price
    
    inversion_inicial = 500000 
    flujo_caja_horario = ventas_por_hora - costo_servicios - costo_materia_prima
    flujo_caja_anual = flujo_caja_horario * 8000
    
    roi = (flujo_caja_anual / inversion_inicial) * 100 if inversion_inicial > 0 else 0
    payback = inversion_inicial / flujo_caja_anual if flujo_caja_anual > 0 else float('inf')
    npv = -inversion_inicial + (flujo_caja_anual / 0.1) 
    
    costo_real_produccion = (costo_servicios + costo_materia_prima) / prod.F_mass if prod.F_mass > 0 else 0

    return sys, prod, {"ROI": roi, "Payback": payback, "NPV": npv, "Costo_Real": costo_real_produccion}

# ===============================================
# 2. INTERFAZ STREAMLIT
# ===============================================
st.title("👨‍🔬 Simulador de Etanol & Tutor IA")

with st.sidebar:
    st.header("🎮 Controles de Proceso")
    t_mosto = st.slider("Temp. Alimentación Mosto (°C)", 10, 50, 25)
    t_w220 = st.slider("Temp. Salida W220 (°C)", 70, 110, 95)
    p_v100 = st.slider("Presión Flash V100 (atm)", 0.5, 5.0, 1.0)
    
    st.header("💰 Precios y Finanzas")
    p_luz = st.slider("Precio Luz ($/kWh)", 0.05, 0.50, 0.15)
    p_vapor = st.slider("Precio Vapor ($/ton)", 10.0, 50.0, 20.0)
    p_agua = st.slider("Precio Agua ($/ton)", 0.5, 5.0, 1.5)
    p_mosto_in = st.slider("Costo Mosto ($/kg)", 0.1, 2.0, 0.5)
    p_etanol_vta = st.slider("Venta Etanol ($/kg)", 1.0, 5.0, 2.5)
    
    st.markdown("---")
    tutor_ia = st.toggle("🤖 Modo Tutor IA")
    
    st.markdown("### Presiona para actualizar:")
    boton_ejecutar = st.button("🚀 EJECUTAR SIMULACIÓN", use_container_width=True)

params = {
    't_mosto': t_mosto, 't_w220_out': t_w220, 'p_v100': p_v100,
    'p_luz': p_luz, 'p_vapor': p_vapor, 'p_agua': p_agua,
    'p_mosto_in': p_mosto_in, 'p_etanol_vta': p_etanol_vta
}

# CORRECCIÓN 2: Control de estado para no perder la simulación al usar el chat
if boton_ejecutar:
    st.session_state['simulacion_activa'] = True

if st.session_state.get('simulacion_activa', False):
    try:
        sys, producto, econ = run_simulation(params)
        
        # 3. MÉTRICAS
        st.subheader("📦 Corriente de Producto Final")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Temperatura", f"{producto.T - 273.15:.2f} °C")
        c2.metric("Presión", f"{producto.P / 101325:.2f} atm")
        c3.metric("Flujo Másico", f"{producto.F_mass:.2f} kg/h")
        pureza = (producto.imass['Ethanol']/producto.F_mass)*100 if producto.F_mass > 0 else 0
        c4.metric("Comp. Etanol", f"{pureza:.1f} %")
        
        st.subheader("📈 Resultados Financieros")
        e1, e2, e3, e4 = st.columns(4)
        e1.metric("Costo Real", f"${econ['Costo_Real']:.2f} /kg")
        e2.metric("Sugerencia Venta", f"${econ['Costo_Real'] * 1.3:.2f} /kg")
        e3.metric("Payback", f"{econ['Payback']:.2f} años" if econ['Payback'] != float('inf') else "---")
        e4.metric("ROI Anual", f"{econ['ROI']:.1f} %")
        st.info(f"NPV (Valor Presente Neto): ${econ['NPV']:,.2f}")

        # 4. TABLAS
        st.markdown("---")
        col_m, col_e = st.columns(2)
        with col_m:
            st.subheader("📊 Materia")
            df_mat = pd.DataFrame([{ "ID": s.ID, "kg/h": round(s.F_mass,2), "°C": round(s.T-273.15,1)} for s in sys.streams if s.F_mass > 0])
            st.dataframe(df_mat, use_container_width=True)
        with col_e:
            st.subheader("⚡ Energía")
            # CORRECCIÓN 3: Evitar colapso si un equipo devuelve NoneType en duty
            df_en = pd.DataFrame([{ "Equipo": u.ID, "kW": round(sum([h.duty for h in u.heat_utilities if getattr(h, 'duty', None) is not None])/3600,2) if u.heat_utilities else 0} for u in sys.units])
            st.dataframe(df_en, use_container_width=True)

        # 5. TUTOR IA
        if tutor_ia and "GEMINI_API_KEY" in st.secrets:
            st.markdown("---")
            st.subheader("🤖 Tutor IA (Gemini)")
            genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
            
            model = genai.GenerativeModel('gemini-2.5-pro')
            
            if "messages" not in st.session_state: st.session_state.messages = []
            for msg in st.session_state.messages:
                with st.chat_message(msg["role"]): st.markdown(msg["content"])
            
            if prompt := st.chat_input("Pregunta algo sobre el proceso..."):
                st.session_state.messages.append({"role": "user", "content": prompt})
                with st.chat_message("user"): st.markdown(prompt)
                
                contexto = f"Proceso: Etanol. ROI: {econ['ROI']}%. Pureza: {pureza}%. El estudiante pregunta: {prompt}"
                with st.chat_message("assistant"):
                    response = model.generate_content(contexto)
                    st.markdown(response.text)
                    st.session_state.messages.append({"role": "assistant", "content": response.text})

    except Exception as e:
        st.error(f"Error en la simulación: {e}")

else:
    st.warning("👈 Ajusta los parámetros en la barra lateral y presiona el botón 'EJECUTAR SIMULACIÓN'.")

# ===============================================
# 6. DIAGRAMAS DE INGENIERÍA (AutoCAD Plant 3D)
# ===============================================
st.markdown("---")
st.header("📐 Diagramas de Planta (Estándar ISO)")

def mostrar_pdf(ruta_archivo):
    """Función auxiliar para rasterizar un PDF a imagen usando PyMuPDF y asegurar su visualización"""
    try:
        doc = fitz.open(ruta_archivo)
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            
            mat = fitz.Matrix(2, 2) 
            pix = page.get_pixmap(matrix=mat)
            
            img_bytes = pix.tobytes("png")
            st.image(img_bytes, caption=f"Página {page_num + 1} - Renderizado desde Plant 3D", use_container_width=True)
        
        with open(ruta_archivo, "rb") as f:
            pdf_data = f.read()
            
        st.download_button(
            label="⬇️ Descargar archivo original en PDF",
            data=pdf_data,
            file_name=ruta_archivo,
            mime="application/pdf",
            key=f"btn_{ruta_archivo}"
        )
        
    except FileNotFoundError:
        st.warning(f"⚠️ No se encontró el archivo: {ruta_archivo}. Verifica que esté en la raíz del repositorio.")
    except Exception as e:
        st.error(f"❌ Error interno al renderizar el documento: {e}")

tab1, tab2 = st.tabs(["11. Diagrama de Bloques", "12. Diagrama de Flujo de Proceso"])


    """
    Incrusta el SVG directamente en el código para evitar errores de 'archivo no encontrado'.
    Mapea las propiedades calculadas a hitboxes interactivos sobre los rombos.
    """
    
    # CÓDIGO SVG ORIGINAL PROPORCIONADO POR EL USUARIO (INCRUSTADO)
    # Nota: Se agregó viewBox="0 0 2524 1151.96" a la etiqueta <svg> original para hacerlo responsivo.
    base_svg_content = """
        <svg viewBox="0 0 2524 1151.96" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:lucid="lucid" width="2524" height="1151.96"><g transform="translate(322 -140)" lucid:page-tab-id="0_0"><path d="M95.2 271.1l-10.1 14.03a3.05 3.05 0 0 0 2.5 4.83l68.6-.46a2.94 2.94 0 0 0 2.3-4.76L145.4 268.1a.78.78 0 0 0-.82-.3l-.5.15" stroke="#00b49c" stroke-width="4" fill-opacity="0"/><path d="M83 221.96s2.3-3.7 3.3-5.13c1.1-1.44 1.2-2.25 4-5.04 2.9-2.7 5.5-4.87 9.2-6.67 3.8-1.9 5.9-3.7 12-4.32 6.1-.7 7.9-.8 7.9-.8l54.6.16a6 6 0 0 1 5.98 6.02l-.06 22.1a6 6 0 0 1-6.02 6l-16.5-.08s.4 3.15.4 5.04c0 1.98.2 3.96-.5 6.93-.6 2.97-1.2 5.58-3 9.18-1.8 3.6-3.5 6.3-4.8 7.83-1.3 1.62-2.2 2.7-4.9 5.04-2.7 2.43-4.3 3.6-7.4 5.13-3.1 1.62-4.9 2.6-7.1 3.15-2.3.63-3 1.08-6.6 1.44-3.6.27-5.9.45-8.3.27-2.4-.26-5.1-.53-7.2-1.25-2.2-.72-5.8-2.16-7.8-3.24-2.1-1.07-2.3-.7-5.1-2.87-2.8-2.16-3.5-1.98-5.9-5.04-2.5-2.98-4.8-5.77-6.2-9.2-1.4-3.4-3-8.36-3-8.36" stroke="#00b49c" stroke-width="4" fill-opacity="0"/><path d="M105.4 229.7s1-1.44 1.9-2.25c1-.8 2.1-1.62 3.4-2.34 1.3-.7 2.4-1.25 4-1.6 1.7-.46 4.5-.55 4.5-.55s3.1.18 4.8 1c1.8.7 4.1 1.8 5.6 3.4 1.5 1.72 2 2.35 2.7 3.6.8 1.36 1.3 2.08 1.8 3.8.4 1.7.7 3.23.7 4.13 0 1 .1 1.62-.1 2.97-.3 1.44-.1 1.7-.7 3.15-.5 1.44-.4 1.62-1.4 3.15-.9 1.53-1 1.9-2 2.97-1.1 1.17-1.4 1.62-2.6 2.43-1.3.8-1.6 1.17-3.4 1.8-1.8.72-2.1.9-3.5 1.08-1.3.18-1.9.18-3.1.18-1.3-.08-1.6 0-3-.35-1.3-.36-2.9-.9-2.9-.9s-1-.45-2-1.08c-1-.63-1.1-.54-2.3-1.7-1.2-1.27-1.7-1.36-2.7-3.07l-.9-1.7M506 440a6 6 0 0 0-6 6v68a6 6 0 0 0 6 6h188a6 6 0 0 0 6-6v-68a6 6 0 0 0-6-6zM520 440v80m160-80v80m-160-60h160m-160 20h160m-160 20h160M1000 720c0 22.08-17.92 40-40 40s-40-17.92-40-40 17.92-40 40-40 40 17.92 40 40z" stroke="#00b49c" stroke-width="4" fill-opacity="0"/><path d="M961.28 680v7.6l21.04 12.64-42.08 29.44 21.04 21.04V760" stroke="#00b49c" stroke-width="4" fill="none"/><path d="M1280 920v120c0 11.05 17.9 20 40 20s40-8.95 40-20V920c0-11.05-17.9-20-40-20s-40 8.95-40 20zM1575.2 1271.1l-10.1 14.03a3.05 3.05 0 0 0 2.5 4.83l68.6-.46a2.94 2.94 0 0 0 2.3-4.76l-13.08-16.65a.78.78 0 0 0-.82-.28l-.5.13" stroke="#00b49c" stroke-width="4" fill-opacity="0"/><path d="M1563 1221.96s2.3-3.7 3.3-5.13c1.1-1.44 1.2-2.25 4-5.04 2.9-2.7 5.5-4.87 9.2-6.67 3.8-1.9 5.9-3.7 12-4.32 6.1-.7 7.9-.8 7.9-.8l54.6.16a6 6 0 0 1 5.98 6.02l-.06 22.1a6 6 0 0 1-6.02 6l-16.5-.08s.4 3.15.4 5.04c0 1.98.2 3.96-.5 6.93-.6 2.97-1.2 5.58-3 9.18-1.8 3.6-3.5 6.3-4.8 7.83-1.3 1.62-2.2 2.7-4.9 5.04-2.7 2.43-4.3 3.6-7.4 5.13-3.1 1.62-4.9 2.6-7.1 3.15-2.3.63-3 1.08-6.6 1.44-3.6.27-5.9.45-8.3.27-2.4-.26-5.1-.53-7.2-1.25-2.2-.72-5.8-2.16-7.8-3.24-2.1-1.07-2.3-.7-5.1-2.87-2.8-2.16-3.5-1.98-5.9-5.04-2.5-2.98-4.8-5.77-6.2-9.2-1.4-3.4-3-8.36-3-8.36" stroke="#00b49c" stroke-width="4" fill-opacity="0"/><path d="M1585.4 1229.7s1-1.44 1.9-2.25c1-.8 2.1-1.62 3.4-2.34 1.3-.7 2.4-1.25 4-1.6 1.7-.46 4.5-.55 4.5-.55s3.1.18 4.8 1c1.8.7 4.1 1.8 5.6 3.4 1.5 1.72 2 2.35 2.7 3.6.8 1.36 1.3 2.08 1.8 3.8.4 1.7.7 3.23.7 4.13 0 1 .1 1.62-.1 2.97-.3 1.44-.1 1.7-.7 3.15-.5 1.44-.4 1.62-1.4 3.15-.9 1.53-1 1.9-2 2.97-1.1 1.17-1.4 1.62-2.6 2.43-1.3.8-1.6 1.17-3.4 1.8-1.8.72-2.1.9-3.5 1.08-1.3.18-1.9.18-3.1.18-1.3-.08-1.6 0-3-.35-1.3-.36-2.9-.9-2.9-.9s-1-.45-2-1.08c-1-.63-1.1-.54-2.3-1.7-1.2-1.27-1.7-1.36-2.7-3.07l-.9-1.7" stroke="#00b49c" stroke-width="4" fill-opacity="0"/><path d="M1320 896V614.73a6 6 0 0 1 6-6h60.82" stroke="#86e819" stroke-width="4" fill="none"/><path d="M1321.97 898.07l-2-.07-1.94.12v-2.17h3.94zM1388.92 607.48l-.1 1.25.1 1.25.17.73h-2.33v-3.94h2.33z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M1449.87 608.73h95.47" stroke="#86e819" stroke-width="4" fill="none"/><path d="M1449.92 610.7h-2.33l.17-.72.1-1.25-.1-1.25-.18-.72h2.32z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M1561.6 608.73l-14.26 4.64v-9.27z" stroke="#86e819" stroke-width="4" fill="#86e819"/><path d="M1320 1064v164.1a6 6 0 0 0 6 6h60.82" stroke="#86e819" stroke-width="4" fill="none"/><path d="M1321.97 1064.05h-3.94v-2.12l2 .07 1.94-.12zM1388.92 1232.86l-.1 1.25.1 1.27.17.72h-2.33v-3.96h2.33z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M1449.87 1234.1h89" stroke="#86e819" stroke-width="4" fill="none"/><path d="M1449.92 1236.1h-2.33l.17-.73.1-1.26-.1-1.24-.18-.72h2.32z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M1555.15 1234.1l-14.27 4.65v-9.27z" stroke="#86e819" stroke-width="4" fill="#86e819"/><path d="M960 901.52V974a6 6 0 0 0 6 6h289.26" stroke="#86e819" stroke-width="4" fill="none"/><path d="M958.75 899.42l1.25.1 1.25-.1.73-.17v2.32H958v-2.32z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M1271.53 980l-14.27 4.63v-9.26z" stroke="#86e819" stroke-width="4" fill="#86e819"/><path d="M960 764v74.48" stroke="#86e819" stroke-width="4" fill="none"/><path d="M959.97 762l2-.07v2.12h-3.95v-2.17zM961.98 840.75l-.73-.17-1.25-.1-1.25.1-.73.17v-2.32H962z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M704 480h94.48" stroke="#86e819" stroke-width="4" fill="none"/><path d="M704.05 481.98H702v-3.96h2.05zM800.58 478.75l-.1 1.25.1 1.25.17.73h-2.32v-3.96h2.32z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M861.52 480h93.82a6 6 0 0 1 6 6v169.34" stroke="#86e819" stroke-width="4" fill="none"/><path d="M861.57 481.98h-2.32l.17-.73.1-1.25-.1-1.25-.17-.73h2.32z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M961.34 671.6l-4.63-14.26H966z" stroke="#86e819" stroke-width="4" fill="#86e819"/><path d="M330 381.52V474a6 6 0 0 0 6 6h139.26" stroke="#86e819" stroke-width="4" fill="none"/><path d="M328.75 379.42l1.25.1 1.25-.1.73-.17v2.32h-3.96v-2.32z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M491.53 480l-14.27 4.63v-9.26z" stroke="#86e819" stroke-width="4" fill="#86e819"/><path d="M161.55 245H324a6 6 0 0 1 6 6v67.48" stroke="#86e819" stroke-width="4" fill="none"/><path d="M161.6 246.97h-2.43l.1-.4.24-1.23.28-2.3h1.82zM331.98 320.75l-.73-.17-1.25-.1-1.25.1-.73.17v-2.32h3.96z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M1641.55 1245H1854a6 6 0 0 0 6-6V346a6 6 0 0 0-6-6H659.05a6 6 0 0 0-6 6v69.26" stroke="#86e819" stroke-width="4" fill="none"/><path d="M1641.6 1246.97h-2.43l.1-.4.24-1.23.28-2.3h1.82z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M653.05 431.53l-4.63-14.27h9.27z" stroke="#86e819" stroke-width="4" fill="#86e819"/><path d="M1380 964.6a6 6 0 0 1 6-6h91.36a6 6 0 0 1 6 6v30.8a6 6 0 0 1-6 6H1386a6 6 0 0 1-6-6z" fill="none"/><use xlink:href="#a" transform="matrix(1,0,0,1,1385,963.58875) translate(0 25.99435763888889)"/><path d="M480 386a6 6 0 0 1 6-6h91.36a6 6 0 0 1 6 6v30.82a6 6 0 0 1-6 6H486a6 6 0 0 1-6-6z" fill="none"/><use xlink:href="#b" transform="matrix(1,0,0,1,485,385) translate(0 25.99435763888889)"/><path d="M80 146a6 6 0 0 1 6-6h91.36a6 6 0 0 1 6 6v30.82a6 6 0 0 1-6 6H86a6 6 0 0 1-6-6z" fill="none"/><use xlink:href="#c" transform="matrix(1,0,0,1,85,145) translate(0 25.99435763888889)"/><path d="M1000 652.55a6 6 0 0 1 6-6h91.36a6 6 0 0 1 6 6v30.83a6 6 0 0 1-6 6H1006a6 6 0 0 1-6-6z" fill="none"/><use xlink:href="#d" transform="matrix(1,0,0,1,1005,651.5525) translate(0 25.99435763888889)"/><path d="M1556.64 1146a6 6 0 0 1 6-6H1654a6 6 0 0 1 6 6v30.82a6 6 0 0 1-6 6h-91.36a6 6 0 0 1-6-6z" fill="none"/><use xlink:href="#e" transform="matrix(1,0,0,1,1561.6414756944444,1145) translate(0 25.99435763888889)"/><path d="M1650 533.18a6 6 0 0 1 6-6h91.36a6 6 0 0 1 6 6V564a6 6 0 0 1-6 6H1656a6 6 0 0 1-6-6z" fill="none"/><use xlink:href="#f" transform="matrix(1,0,0,1,1655,532.1775) translate(0 25.99435763888889)"/><path d="M1650 606.55c0 22.08-17.92 40-40 40s-40-17.92-40-40 17.92-40 40-40 40 17.92 40 40z" stroke="#00b49c" stroke-width="4" fill-opacity="0"/><path d="M1570 608.63h8.4l12.64-21.04 29.52 42.07 21.04-21.04h8.4" stroke="#00b49c" stroke-width="4" fill="none"/><path d="M543.3 524v70a6 6 0 0 1-6 6H218.2" stroke="#86e819" stroke-width="4" fill="none"/><path d="M545.3 524.05h-3.96V522h3.95zM218.26 601.98h-2.32l.17-.73.1-1.25-.1-1.25-.16-.73h2.32z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M155.17 600h-452.43" stroke="#86e819" stroke-width="4" fill="none"/><path d="M157.26 598.75l-.1 1.25.1 1.25.18.73h-2.32V598h2.32z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M-313.53 600l14.27-4.63v9.26z" stroke="#86e819" stroke-width="4" fill="#86e819"/><path d="M-318 720h5.97m7.95 0h11.94m7.95 0h11.95m7.95 0h11.94m7.95 0h11.93m7.95 0h11.93m7.97 0h11.93m7.96 0h11.93m7.95 0h11.93m7.96 0h11.93m7.96 0h11.93m7.96 0h11.93m7.96 0h11.94m7.96 0h11.93m7.96 0h11.92m7.96 0h11.93m7.96 0H6.2m7.95 0H26.1m7.95 0h11.93m7.95 0h11.94m7.95 0h11.94m7.95 0h11.95m7.95 0h11.94m7.95 0h11.93m7.95 0h11.94m7.95 0h11.94m7.96 0h11.93m7.95 0h11.93m7.96 0h11.93m7.96 0h11.93m7.96 0h11.93m7.96 0h11.94m7.96 0h11.93m7.96 0h11.92m7.96 0h11.93m7.97 0h11.93m7.96 0H404m7.94 0h11.94m7.95 0h11.94m7.95 0h11.94m7.95 0h11.95m7.95 0h11.94m7.95 0h11.93m7.95 0h11.94m7.95 0h11.94m7.96 0H583m7.95 0h11.93m7.96 0h11.93m7.96 0h11.93m7.96 0h11.93m7.96 0h11.94m7.96 0h11.93m7.96 0h11.92m7.96 0h11.93m7.97 0H762m7.96 0h11.93m7.95 0h11.93m7.96 0h11.93m7.96 0h11.93m7.96 0h11.93m7.95 0h11.94m7.95 0h5.96M-317.95 720H-320" stroke="#86e819" stroke-width="4" fill="none"/><path d="M911.53 720l-14.27 4.63v-9.26z" stroke="#86e819" stroke-width="4" fill="#86e819"/><path d="M1004 720h5.97m7.95 0h11.93m7.96 0h11.94m7.95 0h11.92m7.96 0h11.93m7.96 0h11.94m7.95 0h11.93m7.96 0h11.93m7.95 0h11.93m7.96 0h11.94m7.95 0h11.92m7.96 0h11.93m7.96 0h11.94m7.95 0h11.93m7.96 0h11.93m7.95 0h11.93m7.96 0h11.94m7.95 0h11.93m7.95 0h11.93m7.96 0h11.94m7.95 0h11.93m7.96 0h11.93m7.95 0h11.93m7.96 0h11.94m7.95 0h11.93m7.95 0h11.93m7.97 0H1527m7.95 0h11.93m7.96 0h11.93m7.95 0h11.93m7.96 0h11.94m7.95 0h11.93m7.95 0h11.93m7.97 0h11.93m7.95 0h11.93m7.96 0h11.93m7.95 0h11.93m7.96 0h11.94m7.95 0h11.93m7.95 0h11.93m7.97 0h11.93m7.95 0h11.93m7.96 0h11.93m7.95 0h11.94m7.95 0h11.94m7.95 0h11.93m7.95 0h11.93m7.97 0h11.93m7.95 0h11.93m7.96 0h11.93m7.95 0h11.94m7.95 0h11.94m7.96 0h11.93m7.95 0h11.93m7.97 0h11.93m7.95 0h11.93m7.96 0h11.93m7.95 0h11.94m7.95 0h11.94m7.96 0h5.96" stroke="#86e819" stroke-width="4" fill="none"/><path d="M1004.05 721.98h-2.17l.12-1.95-.07-2h2.12z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M2193.53 720l-14.27 4.63v-9.26z" stroke="#86e819" stroke-width="4" fill="#86e819"/><path d="M2198 440h-6.02m-8.03 0h-12.04m-8.02 0h-12.04m-8.03 0h-12.03m-8.03 0h-12.04m-8.02 0h-12.05m-8.02 0h-12.03m-8.03 0h-12.04m-8.03 0h-12.04m-8.03 0h-12.04m-8.03 0h-12.04m-8.04 0h-12.04m-8.03 0h-12.05m-8.03 0h-12.04m-8.02 0H1911m-8 0h-12.06m-8.02 0h-12.04m-8.03 0h-12.04m-8.02 0h-12.04m-8.03 0h-12.03m-8.03 0h-12.04m-8.03 0h-12.04m-8.03 0h-12.04m-8.03 0h-12.04m-8.03 0h-12.05m-8.02 0h-12.04m-8.03 0h-12.04m-8.04 0h-12.04m-8.03 0h-12.05m-8.03 0H1616a6 6 0 0 0-6 6v5.75m0 7.66v11.5m0 7.68v11.5m0 7.66v11.5m0 7.66v11.5m0 7.66v5.75M2197.95 440h2.05" stroke="#86e819" stroke-width="4" fill="none"/><path d="M1610 558.08l-4.63-14.27h9.27z" stroke="#86e819" stroke-width="4" fill="#86e819"/><path d="M1610 650.55v6.13m0 8.18v12.25m0 8.2v12.25m0 8.17v12.25m0 8.18v12.26m0 8.18v12.26m0 8.17v12.27m0 8.17v12.26m0 8.17V814a6 6 0 0 0 6 6h6m8.03 0h12.03m8.02 0h12.02m8.02 0h12.03m8.02 0h12.02m8 0h12.04m8.02 0h12.02m8.02 0h12.03m8.02 0h12.02m8.02 0h12.02m8.02 0h12.02m8.02 0h12.03m8.03 0h12.02m8.02 0h12.03m8.02 0h12.03m8 0h12.04m8 0h12.03m8.02 0h12.03m8.02 0h12.03m8 0h12.04m8.02 0h12m8.03 0h12.03m8.02 0H2063m8.02 0h12.03m8.02 0h12.02m8 0h12.04m8.02 0h12.03m8 0h12.03m8.02 0h6" stroke="#86e819" stroke-width="4" fill="none"/><path d="M1609.97 648.55l2-.07v2.13h-3.94v-2.17z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M2193.53 820l-14.27 4.63v-9.26z" stroke="#86e819" stroke-width="4" fill="#86e819"/><path d="M-114.24 208.36a6 6 0 0 1 8.48 0l21.52 21.5a6 6 0 0 1 0 8.5l-21.52 21.5a6 6 0 0 1-8.48 0l-21.52-21.5a6 6 0 0 1 0-8.5z" stroke="#86e819" stroke-width="4" fill-opacity="0"/><use xlink:href="#g" transform="matrix(1,0,0,1,-135,209.1141187915582) translate(18.33724826388889 31.78168402777778)"/><path d="M-78.48 234.1H58.88" stroke="#86e819" stroke-width="4" fill="none"/><path d="M-78.43 236.1h-2.32l.17-.73.1-1.26-.1-1.24-.17-.72h2.32z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M75.15 234.1l-14.27 4.65v-9.27z" stroke="#86e819" stroke-width="4" fill="#86e819"/><path d="M-318 234.1h176.48M-317.95 234.1H-320" stroke="#86e819" stroke-width="4" fill="none"/><path d="M-139.42 232.86l-.1 1.25.1 1.27.17.72h-2.32v-3.96h2.32z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M325.76 324.24a6 6 0 0 1 8.48 0l21.52 21.52a6 6 0 0 1 0 8.48l-21.52 21.52a6 6 0 0 1-8.48 0l-21.52-21.52a6 6 0 0 1 0-8.48z" stroke="#86e819" stroke-width="4" fill-opacity="0"/><use xlink:href="#h" transform="matrix(1,0,0,1,305,325) translate(18.33724826388889 31.78168402777778)"/><path d="M825.76 454.24a6 6 0 0 1 8.48 0l21.52 21.52a6 6 0 0 1 0 8.48l-21.52 21.52a6 6 0 0 1-8.48 0l-21.52-21.52a6 6 0 0 1 0-8.48z" stroke="#86e819" stroke-width="4" fill-opacity="0"/><use xlink:href="#i" transform="matrix(1,0,0,1,805,455) translate(18.33724826388889 31.78168402777778)"/><path d="M955.76 844.24a6 6 0 0 1 8.48 0l21.52 21.52a6 6 0 0 1 0 8.48l-21.52 21.52a6 6 0 0 1-8.48 0l-21.52-21.52a6 6 0 0 1 0-8.48z" stroke="#86e819" stroke-width="4" fill-opacity="0"/><use xlink:href="#j" transform="matrix(1,0,0,1,935,845) translate(18.33724826388889 31.78168402777778)"/><path d="M1414.1 582.98a6 6 0 0 1 8.5 0l21.5 21.5a6 6 0 0 1 0 8.5l-21.5 21.5a6 6 0 0 1-8.5 0l-21.5-21.5a6 6 0 0 1 0-8.5z" stroke="#86e819" stroke-width="4" fill-opacity="0"/><use xlink:href="#k" transform="matrix(1,0,0,1,1393.3437586805555,583.7325601490112) translate(18.33724826388889 31.78168402777778)"/><path d="M1414.1 1208.36a6 6 0 0 1 8.5 0l21.5 21.5a6 6 0 0 1 0 8.5l-21.5 21.5a6 6 0 0 1-8.5 0l-21.5-21.5a6 6 0 0 1 0-8.5z" stroke="#86e819" stroke-width="4" fill-opacity="0"/><use xlink:href="#l" transform="matrix(1,0,0,1,1393.3437586805555,1209.1141187915582) translate(18.33724826388889 31.78168402777778)"/><path d="M1785.76 580.8a6 6 0 0 1 8.48 0l21.52 21.5a6 6 0 0 1 0 8.5l-21.52 21.5a6 6 0 0 1-8.48 0l-21.52-21.5a6 6 0 0 1 0-8.5z" stroke="#86e819" stroke-width="4" fill-opacity="0"/><use xlink:href="#m" transform="matrix(1,0,0,1,1765,581.5525) translate(18.33724826388889 31.78168402777778)"/><path d="M1654 606.55h104.48" stroke="#86e819" stroke-width="4" fill="none"/><path d="M1654.05 608.53h-2.17l.12-1.95-.07-2h2.12zM1760.58 605.3l-.1 1.25.1 1.25.17.73h-2.32v-3.95h2.32z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M1821.52 606.55h355.74" stroke="#86e819" stroke-width="4" fill="none"/><path d="M1821.57 608.53h-2.32l.17-.73.1-1.25-.1-1.25-.17-.72h2.32z" stroke="#86e819" stroke-width=".05" fill="#86e819"/><path d="M2193.53 606.55l-14.27 4.64v-9.28z" stroke="#86e819" stroke-width="4" fill="#86e819"/><path d="M182.44 574.24a6 6 0 0 1 8.5 0l21.5 21.52a6 6 0 0 1 0 8.48l-21.5 21.52a6 6 0 0 1-8.5 0l-21.5-21.52a6 6 0 0 1 0-8.48z" stroke="#86e819" stroke-width="4" fill-opacity="0"/><use xlink:href="#n" transform="matrix(1,0,0,1,161.68751736111108,575) translate(18.33724826388889 31.78168402777778)"/><defs><path fill="#fff" d="M766 0H467L3-1349h308c102 347 212 687 307 1041 91-356 204-694 304-1041h305" id="o"/><path fill="#fff" d="M324-409v-244h580v244H324" id="p"/><path fill="#fff" d="M117-675c0-408 109-695 502-695 384 0 492 295 492 695 0 326-84 561-303 658-117 51-277 52-394 1-217-95-297-335-297-659zm591 451c148-93 132-449 108-680-15-138-53-246-199-246-150 0-193 107-207 248-22 230-40 585 106 678 50 32 141 32 192 0zM506-555v-249h215v249H506" id="q"/><path fill="#fff" d="M138-1120c207-4 339-93 415-229h266v1140h323V0H149v-209h389v-891c-70 121-216 187-400 194v-214" id="r"/><g id="a"><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,0,0)" xlink:href="#o"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,18.66970486111111,0)" xlink:href="#p"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,37.33940972222222,0)" xlink:href="#q"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,56.00911458333333,0)" xlink:href="#q"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,74.67881944444444,0)" xlink:href="#r"/></g><path fill="#fff" d="M1018 0H770c-49-215-106-420-154-636C567-419 511-214 459 0H211L0-1349h259c35 320 94 617 105 961 37-206 100-393 154-583h195c54 191 118 375 154 583 14-339 68-641 102-961h259" id="s"/><path fill="#fff" d="M135-968c34-256 187-402 480-402 217 0 371 75 441 220 45 93 40 219-8 308-114 212-348 323-508 488-38 39-66 80-85 123h654V0H123v-195c112-245 321-399 515-560 57-48 107-96 142-157 14-26 22-53 22-80-1-104-73-156-185-154-131 2-181 74-199 194" id="t"/><g id="b"><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,0,0)" xlink:href="#s"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,18.66970486111111,0)" xlink:href="#p"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,37.33940972222222,0)" xlink:href="#t"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,56.00911458333333,0)" xlink:href="#r"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,74.67881944444444,0)" xlink:href="#q"/></g><path fill="#fff" d="M616-1349c316 3 526 118 526 426 0 301-201 442-514 447H431V0H136v-1349h480zm-25 646c164 1 254-62 254-215 0-157-99-200-262-202H431v417h160" id="u"/><g id="c"><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,0,0)" xlink:href="#u"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,18.66970486111111,0)" xlink:href="#p"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,37.33940972222222,0)" xlink:href="#r"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,56.00911458333333,0)" xlink:href="#q"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,74.67881944444444,0)" xlink:href="#q"/></g><g id="d"><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,0,0)" xlink:href="#s"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,18.66970486111111,0)" xlink:href="#p"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,37.33940972222222,0)" xlink:href="#t"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,56.00911458333333,0)" xlink:href="#t"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,74.67881944444444,0)" xlink:href="#q"/></g><g id="e"><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,0,0)" xlink:href="#u"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,18.66970486111111,0)" xlink:href="#p"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,37.33940972222222,0)" xlink:href="#t"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,56.00911458333333,0)" xlink:href="#q"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,74.67881944444444,0)" xlink:href="#q"/></g><path fill="#fff" d="M788-691c191 26 337 112 337 315 0 286-214 399-510 399-306 0-483-128-522-391l286-25c18 124 91 190 235 188 132-2 223-58 223-188 0-176-191-182-378-179v-227c176 5 345-9 345-176 0-115-82-171-200-171s-198 55-206 169l-281-20c34-252 213-373 492-373 224 0 381 74 451 224 20 43 29 89 29 136 0 195-130 279-301 315v4" id="v"/><g id="f"><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,0,0)" xlink:href="#s"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,18.66970486111111,0)" xlink:href="#p"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,37.33940972222222,0)" xlink:href="#v"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,56.00911458333333,0)" xlink:href="#r"/><use transform="matrix(0.015190972222222222,0,0,0.015190972222222222,74.67881944444444,0)" xlink:href="#q"/></g><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#r" id="g"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#t" id="h"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#v" id="i"/><path fill="#fff" d="M980-287V0H712v-287H71v-211l595-851h314v853h188v209H980zM712-496c2-204-7-417 9-609-118 224-277 405-414 609h405" id="w"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#w" id="j"/><path fill="#fff" d="M426-814c58-49 145-93 255-90 282 8 442 169 442 450 0 312-203 474-522 474-288 0-453-130-497-372l281-23c24 104 89 172 219 172 153 0 230-91 230-245 0-144-78-226-224-229-94-2-148 42-191 91H145l49-763h847v209H449" id="x"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#x" id="k"/><path fill="#fff" d="M706-877c265 0 409 163 409 431 0 299-171 466-474 466-381 0-516-302-516-692 0-236 47-410 135-526 112-147 319-207 541-154 154 36 236 149 276 306l-265 37c-22-86-75-139-170-139-76 0-135 36-178 108s-64 179-64 318c54-94 164-155 306-155zm-74 678c138 0 201-98 201-239s-67-232-208-232c-125 0-205 73-205 202 0 149 67 269 212 269" id="y"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#y" id="l"/><path fill="#fff" d="M1092-1126C914-878 744-630 663-296c-23 96-34 194-34 296H336c7-366 145-620 298-854 59-90 126-177 197-264H131v-231h961v223" id="z"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#z" id="m"/><path fill="#fff" d="M829-709c170 32 291 130 291 327 0 285-207 402-505 402-296 0-506-116-506-400 0-194 122-293 287-327v-4c-145-37-254-130-254-301 0-257 201-358 469-358 275 0 474 97 474 360 0 168-110 262-256 297v4zm-216-99c126 0 184-66 183-187-1-119-62-179-185-178-121 0-182 60-182 178 0 119 60 187 184 187zm4 630c149 0 213-80 212-227-2-138-77-206-220-206-139 1-209 76-211 210-2 147 70 223 219 223" id="A"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#A" id="n"/></defs></g></svg>
    """

    def get_tooltip(stream_id, custom_label, x_pct, y_pct):
        try:
            row = df_mat[df_mat['ID Corriente'] == stream_id].iloc[0]
            l1 = f"T: {row['Temp (°C)']} °C | P: {row['Presión (bar)']} bar"
            l2 = f"F: {row['Flujo (kg/h)']} kg/h | Et: {row['% Etanol']}"
        except:
            l1 = "T: -- °C | P: -- bar"
            l2 = "F: -- kg/h | Et: --"
            
        return f'''
        <div class="hitbox" style="left: {x_pct}%; top: {y_pct}%;">
            <div class="tooltip-content">
                <strong>{custom_label}</strong><br>
                {l1}<br>
                {l2}
            </div>
        </div>
        '''

    html_code = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <style>
        body {{ background-color: transparent; margin: 0; padding: 0; position: relative; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }}
        .svg-container {{ position: relative; width: 100%; max-width: 1400px; margin: auto; display: block; }}
        
        /* El SVG abarca el contenedor y mantiene proporciones */
        .svg-container svg {{ width: 100%; height: auto; display: block; filter: drop-shadow(0 0 10px rgba(0,0,0,0.4)); }}
        
        /* Zonas circulares invisibles sobre los rombos */
        .hitbox {{
            position: absolute;
            width: 50px; 
            height: 50px;
            transform: translate(-50%, -50%);
            cursor: crosshair;
            border-radius: 50%;
            z-index: 10;
        }}
        
        /* Tooltip profesional animado */
        .tooltip-content {{
            visibility: hidden;
            opacity: 0;
            position: absolute;
            bottom: 115%;
            left: 50%;
            transform: translateX(-50%) translateY(10px);
            background-color: #0f172a;
            color: #cbd5e1;
            border: 1px solid #00b49c;
            border-radius: 6px;
            padding: 12px;
            font-family: 'Courier New', Courier, monospace;
            font-size: 13px;
            white-space: nowrap;
            transition: opacity 0.3s ease, transform 0.3s ease, visibility 0.3s;
            box-shadow: 0 8px 16px rgba(0,0,0,0.6);
            z-index: 20;
            pointer-events: none;
        }}
        
        .hitbox:hover .tooltip-content {{
            visibility: visible;
            opacity: 1;
            transform: translateX(-50%) translateY(0);
        }}
        
        .tooltip-content strong {{ color: #86e819; font-size: 14px; display: block; margin-bottom: 4px; border-bottom: 1px solid #1f2937; padding-bottom: 2px; }}
      </style>
    </head>
    <body>
      <div class="svg-container">
        {base_svg_content}
        
        {get_tooltip("1-MOSTO", "1-MOSTO", 8.4, 7.8)}
        {get_tooltip("s1", "Descarga P-100", 25.8, 18.0)}
        {get_tooltip("3-MOSTO-PRE", "3-MOSTO-PRE", 45.4, 30.0)}
        {get_tooltip("Mezcla-Bifásica", "Entrada Flash", 50.8, 62.5)}
        {get_tooltip("Vapor Caliente", "Vapor Destilado", 71.0, 40.5)}
        {get_tooltip("Vinazas", "Líquido Vinazas", 71.0, 94.5)}
        {get_tooltip("Producto Final", "Producto Condensado", 85.5, 40.5)}
        {get_tooltip("DRENAJE", "Drenaje W-210", 20.0, 39.5)}
        {get_tooltip("Vinazas-Retorno", "Recirculación Térmica", 60.0, 17.5)}
      </div>
    </body>
    </html>
    """
    components.html(html_code, height=500)
with tab1:
    st.subheader("Diagrama de Bloques (BFD)")
    mostrar_pdf("diagrama_bloques.pdf")

with tab2:
    st.subheader("Diagrama de Flujo de Proceso (PFD)")
    mostrar_pdf("diagrama_flujo.pdf")
    
with tab3:
    st.subheader("Diagrama Interactivo")
    render_diagrama_interactivo(st.session_state['df_mat'])
    st.divider()
