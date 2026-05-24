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
        <svg viewBox="0 0 2524 1151.96" xmlns="http://www.w3.org/2000/svg" <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:lucid="lucid" width="2522.5" height="1381.96"><g transform="translate(121 60)" lucid:page-tab-id="0_0"><path d="M132.16 63.2l-7.62 11.76a3.23 3.23 0 0 0 2.74 5l52.48-.4a3.13 3.13 0 0 0 2.54-4.94l-9.98-14.12a.58.58 0 0 0-.64-.22l-.4.12" stroke="#000" stroke-width="4" fill="#fff"/><path d="M122.4 19.52s1.84-3.28 2.64-4.56c.88-1.28.96-2 3.2-4.48 2.32-2.4 4.4-4.32 7.36-5.92 3.04-1.68 4.72-3.28 9.6-3.84 4.88-.64 6.32-.72 6.32-.72L194 .14a6 6 0 0 1 5.98 6.02l-.04 18.32a6 6 0 0 1-6.02 5.97l-12-.05s.32 2.8.32 4.48c0 1.76.16 3.52-.4 6.16-.48 2.64-.96 4.96-2.4 8.16s-2.8 5.6-3.84 6.96c-1.04 1.44-1.76 2.4-3.92 4.48-2.16 2.16-3.44 3.2-5.92 4.56-2.48 1.44-3.92 2.32-5.68 2.8-1.84.56-2.4.96-5.28 1.28-2.88.24-4.72.4-6.64.24-1.92-.24-4.08-.48-5.76-1.12-1.76-.64-4.64-1.92-6.24-2.88-1.68-.96-1.84-.64-4.08-2.56s-2.8-1.76-4.72-4.48c-2-2.64-3.84-5.12-4.96-8.16s-2.4-7.44-2.4-7.44" stroke="#000" stroke-width="4" fill="#fff"/><path d="M140.32 26.4s.8-1.28 1.52-2c.8-.72 1.68-1.44 2.72-2.08 1.04-.64 1.92-1.12 3.2-1.44 1.36-.4 3.6-.48 3.6-.48s2.48.16 3.84.88c1.44.64 3.28 1.6 4.48 3.04 1.2 1.52 1.6 2.08 2.16 3.2.64 1.2 1.04 1.84 1.44 3.36.32 1.52.56 2.88.56 3.68 0 .88.08 1.44-.08 2.64-.24 1.28-.08 1.52-.56 2.8-.4 1.28-.32 1.44-1.12 2.8-.72 1.36-.8 1.68-1.6 2.64-.88 1.04-1.12 1.44-2.08 2.16-1.04.72-1.28 1.04-2.72 1.6-1.44.64-1.68.8-2.8.96-1.04.16-1.52.16-2.48.16-1.04-.08-1.28 0-2.4-.32-1.04-.32-2.32-.8-2.32-.8s-.8-.4-1.6-.96c-.8-.56-.88-.48-1.84-1.52-.96-1.12-1.36-1.2-2.16-2.72l-.72-1.52M860 580c0 33.12-26.88 60-60 60-33.12 0-60-26.88-60-60 0-33.12 26.88-60 60-60 33.12 0 60 26.88 60 60z" stroke="#000" stroke-width="4" fill="#fff"/><path d="M743.84 557.92h90.84l-54.24 22.68 54.24 22.8-90.84-1.32" stroke="#000" stroke-width="4" fill="none"/><path d="M583 360h98" stroke="#3a414a" stroke-width="2" fill="none"/><path d="M583.03 360.98H582v-1.96h1.03zM682 360l.1.98h-1.13v-1.96h1.12z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M759 360h35a6 6 0 0 1 6 6v133.5" stroke="#3a414a" stroke-width="2" fill="none"/><path d="M759.03 360.98h-1.12l.1-.98-.1-.98h1.13z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M800 514.76l-4.63-14.26h9.26z" stroke="#3a414a" stroke-width="2" fill="#3a414a"/><path d="M800 643v231a6 6 0 0 0 6 6h333.5" stroke="#3a414a" stroke-width="2" fill="none"/><path d="M800 642l.98-.03v1.06H799v-1.07z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M1154.76 880l-14.26 4.63v-9.26z" stroke="#3a414a" stroke-width="2" fill="#3a414a"/><path d="M-119 30h80M-118.97 30H-120" stroke="#3a414a" stroke-width="2" fill="none"/><path d="M-38 30l.1.98h-1.13v-1.96h1.12z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M39 30h40.5a.16.16 0 0 1 .15.16.16.16 0 0 0 .16.16h23" stroke="#3a414a" stroke-width="2" fill="none"/><path d="M39.03 30.98H37.9L38 30l-.1-.98h1.13z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M118.05 30.32l-14.26 4.64V25.7z" stroke="#3a414a" stroke-width="2" fill="#3a414a"/><path d="M185.02 40H274a6 6 0 0 1 6 6v114" stroke="#3a414a" stroke-width="2" fill="none"/><path d="M185.04 40.98h-1.16l.27-1.95h.9zM280.98 161.06L280 161l-.98.06v-1.1h1.96z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M280 220v134a6 6 0 0 0 6 6h73.5" stroke="#3a414a" stroke-width="2" fill="none"/><path d="M280 219l.98-.06v1.1h-1.96v-1.1z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M374.76 360l-14.26 4.63v-9.26z" stroke="#3a414a" stroke-width="2" fill="#3a414a"/><path d="M1200 797V498.7a6 6 0 0 1 6-6h215" stroke="#3a414a" stroke-width="2" fill="none"/><path d="M1200.97 798.03l-1-.03-.94.06v-1.1h1.94zM1422 492.7l.1.97h-1.13v-1.95h1.12z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M1499 492.7h200.6" stroke="#3a414a" stroke-width="2" fill="none"/><path d="M1499.03 493.67h-1.12l.1-.97-.1-.98h1.13z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M1714.86 492.7l-14.27 4.63v-9.27z" stroke="#3a414a" stroke-width="2" fill="#3a414a"/><path d="M1713.68 1303.2l-8.7 11.95a3.03 3.03 0 0 0 2.46 4.8l60.54-.4a2.92 2.92 0 0 0 2.27-4.73l-11.37-14.3a.7.7 0 0 0-.74-.24l-.45.12" stroke="#000" stroke-width="4" fill="#fff"/><path d="M1702.7 1259.52s2.07-3.28 2.97-4.56c1-1.28 1.08-2 3.6-4.48 2.6-2.4 4.95-4.32 8.28-5.92 3.42-1.68 5.3-3.28 10.8-3.84 5.5-.64 7.1-.72 7.1-.72l48.55.14a6 6 0 0 1 5.98 6.02l-.05 18.32a6 6 0 0 1-6.02 5.98l-14.24-.06s.36 2.8.36 4.48c0 1.76.18 3.52-.45 6.16-.54 2.64-1.08 4.96-2.7 8.16s-3.15 5.6-4.32 6.96c-1.17 1.44-1.98 2.4-4.4 4.48-2.44 2.16-3.88 3.2-6.67 4.56-2.8 1.44-4.4 2.32-6.4 2.8-2.06.56-2.7.96-5.93 1.28-3.24.24-5.3.4-7.47.24-2.16-.24-4.6-.48-6.48-1.12-1.98-.64-5.22-1.92-7.02-2.88-1.9-.96-2.07-.64-4.6-2.56-2.5-1.92-3.14-1.76-5.3-4.48-2.25-2.64-4.32-5.12-5.58-8.16-1.26-3.04-2.7-7.44-2.7-7.44" stroke="#000" stroke-width="4" fill="#fff"/><path d="M1722.86 1266.4s.9-1.28 1.7-2c.9-.72 1.9-1.44 3.07-2.08 1.17-.64 2.16-1.12 3.6-1.44 1.53-.4 4.05-.48 4.05-.48s2.8.16 4.32.88c1.62.64 3.7 1.6 5.04 3.04 1.35 1.52 1.8 2.08 2.43 3.2.72 1.2 1.17 1.84 1.62 3.36.35 1.52.62 2.88.62 3.68 0 .88.1 1.44-.1 2.64-.26 1.28-.08 1.52-.62 2.8-.45 1.28-.36 1.44-1.26 2.8-.8 1.36-.9 1.68-1.8 2.64-1 1.04-1.26 1.44-2.34 2.16-1.17.72-1.44 1.04-3.06 1.6-1.62.64-1.9.8-3.15.96-1.18.16-1.72.16-2.8.16-1.17-.08-1.44 0-2.7-.32-1.17-.32-2.6-.8-2.6-.8s-.9-.4-1.8-.96c-.9-.56-1-.48-2.08-1.52-1.08-1.12-1.53-1.2-2.43-2.72l-.82-1.52" stroke="#000" stroke-width="4" fill="#fff"/><path d="M-119 557.92h10.1m10.1 0h20.22m10.1 0h20.22m10.1 0h20.22m10.1 0h20.22m10.1 0H42.7m10.1 0H73m10.12 0h20.2m10.12 0h20.2m10.1 0h20.23m10.1 0h20.2m10.12 0h20.2m10.1 0h20.22m10.1 0h20.22m10.1 0h20.22m10.1 0h20.2m10.12 0h20.2m10.12 0h20.2m10.1 0h20.23m10.1 0h20.2m10.12 0h20.2m10.12 0h20.2m10.12 0h20.2m10.1 0h20.2m10.12 0h20.2m10.12 0h20.2m10.12 0h20.2m10.1 0h20.23m10.1 0h10.1M-118.97 557.92H-120" stroke="#54c45e" stroke-width="2" fill="none"/><path d="M745.16 557.92l-14.26 4.63v-9.26z" stroke="#54c45e" stroke-width="2" fill="#54c45e"/><path d="M747.4 602.08h-10.3m-10.28 0h-20.58m-10.3 0h-20.57m-10.3 0H644.5m-10.28 0h-20.58m-10.3 0h-20.57m-10.3 0H551.9m-10.3 0h-20.57m-10.3 0h-20.57m-10.3 0H459.3m-10.3 0h-20.57m-10.3 0h-20.57m-10.3 0H366.7m-10.3 0h-20.58m-10.3 0h-10.28a1.04 1.04 0 0 1-1.04-1.04 1.04 1.04 0 0 0-1.04-1.04h-9.87m-9.9 0h-19.73m-9.87 0h-19.75m-9.87 0h-19.75m-9.87 0H184.8m-9.86 0H155.2m-9.88 0h-19.74m-9.88 0H95.96m-9.88 0H66.34m-9.87 0H36.72m-9.87 0H7.1m-9.87 0h-19.75m-9.87 0h-19.73m-9.88 0h-19.75m-9.88 0h-9.87M747.37 602.08h4.03" stroke="#54c45e" stroke-width="2" fill="none"/><path d="M-116.76 600l14.26-4.63v9.26z" stroke="#54c45e" stroke-width="2" fill="#54c45e"/><path d="M1200 963v301a6 6 0 0 0 6 6h215" stroke="#3a414a" stroke-width="2" fill="none"/><path d="M1200.97 963.03h-1.94v-1.06l1 .03.94-.06zM1422 1270l.1.97h-1.13v-1.94h1.12z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M1499 1270h100.57a.16.16 0 0 1 .16.16.16.16 0 0 0 .16.16h83.05" stroke="#3a414a" stroke-width="2" fill="none"/><path d="M1499.03 1270.97h-1.12l.1-.97-.1-.97h1.13z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M1698.2 1270.32l-14.25 4.64v-9.27z" stroke="#3a414a" stroke-width="2" fill="#3a414a"/><path d="M1772.77 1280H2074a6 6 0 0 0 6-6v-118a6 6 0 0 0-6-6H486a6 6 0 0 1-6-6V420.5" stroke="#3a414a" stroke-width="2" fill="none"/><path d="M1772.8 1280.97h-1.18l.3-1.94h.88z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M480 405.24l4.63 14.26h-9.26z" stroke="#3a414a" stroke-width="2" fill="#3a414a"/><path d="M1160 820v120c0 11.05 17.9 20 40 20s40-8.95 40-20V820c0-11.05-17.9-20-40-20s-40 8.95-40 20z" stroke="#000" stroke-width="4" fill="#fff"/><path d="M1239.5 160h1139.88" stroke="#3a414a" stroke-width="3" fill="none"/><path d="M1239.54 161.47h-1.76l.13-.46.1-1-.1-1-.12-.47h1.76z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M2395.15 160l-14.27 4.63v-9.26z" stroke="#3a414a" stroke-width="3" fill="#3a414a"/><path d="M480 316.5V166a6 6 0 0 1 6-6h674.5" stroke="#3a414a" stroke-width="3" fill="none"/><path d="M481.48 318h-2.96v-1.54h2.96zM1162.1 159l-.1 1 .1 1 .12.47h-1.76v-2.94h1.76z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M386 320a6 6 0 0 0-6 6v68a6 6 0 0 0 6 6h188a6 6 0 0 0 6-6v-68a6 6 0 0 0-6-6zM400 320v80m160-80v80m-160-60h160m-160 20h160m-160 20h160M1820 490c0 27.6-22.4 50-50 50s-50-22.4-50-50 22.4-50 50-50 50 22.4 50 50z" stroke="#000" stroke-width="4" fill="#fff"/><path d="M1720 492.6h10.5l15.8-26.3 36.9 52.6 26.3-26.3h10.5" stroke="#000" stroke-width="4" fill="none"/><path d="M1823.5 490h555.88" stroke="#3a414a" stroke-width="3" fill="none"/><path d="M1823.54 491.48h-1.6l.06-1.47-.05-1.48h1.6z" stroke="#3a414a" stroke-width=".05" fill="#3a414a"/><path d="M2395.15 490l-14.27 4.63v-9.26z" stroke="#3a414a" stroke-width="3" fill="#3a414a"/><path d="M1770 437v-11.83m0-11.84v-23.66m0-11.84V366a6 6 0 0 1 6-6h10.1m10.08 0h20.2m10.08 0h20.18m10.1 0h20.18m10.1 0h20.17m10.08 0h20.2m10.08 0h20.18m10.1 0H1998m10.1 0h20.17m10.08 0h20.2m10.08 0h20.18m10.1 0h20.18m10.1 0h20.17m10.08 0h20.18m10.1 0h20.18m10.1 0h20.18m10.1 0h20.17m10.08 0h20.18m10.1 0h20.18m10.1 0h20.18m10.1 0h10.08" stroke="#54c45e" stroke-width="2" fill="none"/><path d="M1770.97 438.05l-.95-.05-1 .03v-1.06h1.95z" stroke="#54c45e" stroke-width=".05" fill="#54c45e"/><path d="M2396.76 360l-14.26 4.63v-9.26z" stroke="#54c45e" stroke-width="2" fill="#54c45e"/><path d="M1770 543v10.34m0 10.33v20.67m0 10.33v20.67m0 10.33V636a6 6 0 0 0 6 6h10.13m10.12 0h20.25m10.13 0h20.24m10.13 0h20.25m10.13 0h20.24m10.13 0H1938m10.13 0h20.24m10.13 0h20.25m10.13 0h20.24m10.13 0h20.25m10.13 0h20.24m10.13 0h20.25m10.13 0h20.24m10.13 0H2181m10.13 0h20.24m10.13 0h20.25m10.12 0h20.25m10.13 0h20.25m10.12 0h20.25m10.13 0h20.25m10.12 0h10.13" stroke="#54c45e" stroke-width="2" fill="none"/><path d="M1769.98 542l1-.03v1.06h-1.95v-1.08z" stroke="#54c45e" stroke-width=".05" fill="#54c45e"/><path d="M2396.8 640.4l-13.62 6.3-1.1-9.2z" stroke="#54c45e" stroke-width="2" fill="#54c45e"/><path d="M-4.8 3.6a8 8 0 0 1 9.6 0l30.4 22.8a4.5 4.5 0 0 1 0 7.2L4.8 56.4a8 8 0 0 1-9.6 0l-30.4-22.8a4.5 4.5 0 0 1 0-7.2z" stroke="#000" stroke-width="2" fill="#fff"/><use xlink:href="#a" transform="matrix(1,0,0,1,-35,5) translate(30.48568576388889 33.24652777777778)"/><path d="M275.2 163.6a8 8 0 0 1 9.6 0l30.4 22.8a4.5 4.5 0 0 1 0 7.2l-30.4 22.8a8 8 0 0 1-9.6 0l-30.4-22.8a4.5 4.5 0 0 1 0-7.2z" stroke="#000" stroke-width="2" fill="#fff"/><use xlink:href="#b" transform="matrix(1,0,0,1,245,165) translate(28.228741319444445 33.24652777777778)"/><path d="M715.2 333.6a8 8 0 0 1 9.6 0l30.4 22.8a4.5 4.5 0 0 1 0 7.2l-30.4 22.8a8 8 0 0 1-9.6 0l-30.4-22.8a4.5 4.5 0 0 1 0-7.2z" stroke="#000" stroke-width="2" fill="#fff"/><use xlink:href="#c" transform="matrix(1,0,0,1,685,335) translate(28.14193576388889 33.24652777777778)"/><path d="M792.96 758.53a8 8 0 0 1 9.6 0l30.4 22.8a4.5 4.5 0 0 1 0 7.2l-30.4 22.8a8 8 0 0 1-9.6 0l-30.4-22.8a4.5 4.5 0 0 1 0-7.2z" stroke="#000" stroke-width="2" fill="#fff"/><use xlink:href="#d" transform="matrix(1,0,0,1,762.7593437689377,759.9283898051206) translate(27.827265625000003 33.24652777777778)"/><path d="M1455.2 466.3a8 8 0 0 1 9.6 0l30.4 22.8a4.5 4.5 0 0 1 0 7.2l-30.4 22.8a8 8 0 0 1-9.6 0l-30.4-22.8a4.5 4.5 0 0 1 0-7.2z" stroke="#000" stroke-width="2" fill="#fff"/><use xlink:href="#e" transform="matrix(1,0,0,1,1425,467.6991066894546) translate(28.25044270833333 33.24652777777778)"/><path d="M1455.2 1243.6a8 8 0 0 1 9.6 0l30.4 22.8a4.5 4.5 0 0 1 0 7.2l-30.4 22.8a8 8 0 0 1-9.6 0l-30.4-22.8a4.5 4.5 0 0 1 0-7.2z" stroke="#000" stroke-width="2" fill="#fff"/><use xlink:href="#f" transform="matrix(1,0,0,1,1425,1245) translate(28.114809027777778 33.24652777777778)"/><path d="M2065.06 461.58a8 8 0 0 1 9.6 0l30.4 22.8a4.5 4.5 0 0 1 0 7.2l-30.4 22.8a8 8 0 0 1-9.6 0l-30.4-22.8a4.5 4.5 0 0 1 0-7.2z" stroke="#000" stroke-width="2" fill="#fff"/><use xlink:href="#g" transform="matrix(1,0,0,1,2034.8567796102411,462.983455213923) translate(28.717022569444445 33.24652777777778)"/><path d="M1195.2 133.6a8 8 0 0 1 9.6 0l30.4 22.8a4.5 4.5 0 0 1 0 7.2l-30.4 22.8a8 8 0 0 1-9.6 0l-30.4-22.8a4.5 4.5 0 0 1 0-7.2z" stroke="#000" stroke-width="2" fill="#fff"/><use xlink:href="#h" transform="matrix(1,0,0,1,1165,135) translate(28.131085069444445 33.24652777777778)"/><path d="M120-54a6 6 0 0 1 6-6h58.5a6 6 0 0 1 6 6v24.9a6 6 0 0 1-6 6H126a6 6 0 0 1-6-6z" fill="none"/><use xlink:href="#i" transform="matrix(1,0,0,1,125,-55) translate(0 21.58376736111111)"/><path d="M503.14 266a6 6 0 0 1 6-6H574a6 6 0 0 1 6 6v24.9a6 6 0 0 1-6 6h-64.86a6 6 0 0 1-6-6z" fill="none"/><use xlink:href="#j" transform="matrix(1,0,0,1,508.1388715277778,265) translate(0 21.58376736111111)"/><path d="M880 567.55a6 6 0 0 1 6-6h69.38a6 6 0 0 1 6 6v24.9a6 6 0 0 1-6 6H886a6 6 0 0 1-6-6z" fill="none"/><use xlink:href="#k" transform="matrix(1,0,0,1,885,566.5509895833334) translate(0 21.58376736111111)"/><path d="M1260 866a6 6 0 0 1 6-6h59.5a6 6 0 0 1 6 6v24.9a6 6 0 0 1-6 6H1266a6 6 0 0 1-6-6z" fill="none"/><use xlink:href="#l" transform="matrix(1,0,0,1,1265,865) translate(0 21.58376736111111)"/><path d="M1707.87 1186a6 6 0 0 1 6-6h62.26a6 6 0 0 1 6 6v24.9a6 6 0 0 1-6 6h-62.26a6 6 0 0 1-6-6z" fill="none"/><use xlink:href="#m" transform="matrix(1,0,0,1,1712.8715190972223,1185) translate(0 21.58376736111111)"/><path d="M1800 406a6 6 0 0 1 6-6h65.53a6 6 0 0 1 6 6v24.9a6 6 0 0 1-6 6H1806a6 6 0 0 1-6-6z" fill="none"/><use xlink:href="#n" transform="matrix(1,0,0,1,1805,405) translate(0 21.58376736111111)"/><defs><path d="M653-1490V0H466v-1314h-10L96-1047v-204l324-239h233" id="o"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#o" id="a"/><path d="M154 0v-137l495-537c165-179 249-281 249-418 0-156-121-253-280-253-170 0-278 110-278 278H158c0-264 200-443 465-443 266 0 455 183 455 416 0 161-73 288-336 568L416-179v12h687V0H154" id="p"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#p" id="b"/><path d="M635 20c-292 0-500-160-510-396h192c11 142 145 229 315 229 187 0 323-105 323-260 0-161-125-274-346-274H488v-165h121c174 0 294-100 294-254 0-148-104-245-266-245-152 0-291 85-297 230H157c8-234 222-395 484-395 278 0 448 188 448 400 0 168-95 291-247 336v12c190 31 301 169 301 357 0 244-216 425-508 425" id="q"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#q" id="c"/><path d="M120-303v-155l652-1032h231v1020h202v167h-202V0H821v-303H120zm702-167v-782h-12L323-482v12h499" id="r"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#r" id="d"/><path d="M626 20c-262 0-458-168-468-396h184c12 133 134 229 284 229 180 0 311-137 311-326 0-192-136-335-323-335-92 0-196 33-255 78l-178-22 88-738h784v167H429l-51 435h8c61-51 160-87 263-87 273 0 474 211 474 499 0 286-210 496-497 496" id="s"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#s" id="e"/><path d="M646 20c-249 0-524-159-524-708 0-524 209-822 547-822 250 0 431 161 467 395H950c-33-129-126-227-281-227-229 0-367 208-367 566h12c80-124 212-198 367-198 255 0 467 205 467 493 0 278-198 501-502 501zm0-167c179 0 318-148 318-334 0-182-133-328-313-328-181 0-322 156-322 330 0 176 134 332 317 332" id="t"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#t" id="f"/><path d="M200 0l662-1311v-12H98v-167h963v177L400 0H200" id="u"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#u" id="g"/><path d="M633 20c-303 0-511-173-511-416 0-188 124-348 291-378v-8c-145-37-237-174-237-332 0-227 192-396 457-396 261 0 456 169 456 396 0 158-94 295-235 332v8c162 30 291 190 291 378 0 243-212 416-512 416zm0-165c197 0 322-103 322-261 0-165-138-283-322-283-188 0-324 118-324 283 0 158 123 261 324 261zm0-703c157 0 272-101 272-252 0-149-110-246-272-246-165 0-273 97-273 246 0 151 112 252 273 252" id="v"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#v" id="h"/><path d="M180 0v-1490h510c348 0 508 209 508 474 0 266-160 477-507 477H370V0H180zm190-706h312c236 0 327-133 327-310 0-176-91-307-329-307H370v617" id="w"/><path d="M798-719v166H144v-166h654" id="x"/><path d="M646 20c-332 0-524-278-524-764 0-483 194-766 524-766s524 283 524 766c0 485-191 764-524 764zm0-166c218 0 341-220 341-598 0-380-123-601-341-601s-341 222-341 601c0 378 123 598 341 598" id="y"/><g id="i"><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#w"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,13.183593749999996,0)" xlink:href="#x"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,23.404947916666664,0)" xlink:href="#o"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,32.443576388888886,0)" xlink:href="#y"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,46.46267361111111,0)" xlink:href="#y"/></g><path d="M458 0L52-1490h194c108 439 233 855 324 1313 92-459 221-873 331-1313h216c110 438 234 854 330 1307 92-455 216-869 323-1307h196L1558 0h-221l-256-944c-26-95-50-200-73-331-22 122-44 223-73 331L680 0H458" id="z"/><g id="j"><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#z"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,20.768229166666668,0)" xlink:href="#x"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,30.240885416666664,0)" xlink:href="#p"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,43.79340277777777,0)" xlink:href="#o"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,52.832031249999986,0)" xlink:href="#y"/></g><g id="k"><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#z"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,20.768229166666668,0)" xlink:href="#x"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,30.240885416666664,0)" xlink:href="#p"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,43.79340277777777,0)" xlink:href="#p"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,57.345920138888886,0)" xlink:href="#y"/></g><path d="M600 0L52-1490h200c166 485 304 806 458 1336 156-533 285-846 449-1336h202L819 0H600" id="A"/><g id="l"><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#A"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,14.20355902777778,0)" xlink:href="#x"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,24.424913194444443,0)" xlink:href="#y"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,38.444010416666664,0)" xlink:href="#y"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,52.463107638888886,0)" xlink:href="#o"/></g><g id="m"><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#w"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,13.183593749999996,0)" xlink:href="#x"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,22.65625,0)" xlink:href="#p"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,36.208767361111114,0)" xlink:href="#y"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,50.22786458333333,0)" xlink:href="#y"/></g><g id="n"><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,0,0)" xlink:href="#z"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,20.768229166666668,0)" xlink:href="#x"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,30.740017361111114,0)" xlink:href="#q"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,44.46614583333333,0)" xlink:href="#o"/><use transform="matrix(0.010850694444444444,0,0,0.010850694444444444,53.50477430555556,0)" xlink:href="#y"/></g></defs></g></svg>
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
