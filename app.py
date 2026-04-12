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

with tab1:
    st.subheader("Diagrama de Bloques (BFD)")
    mostrar_pdf("diagrama_bloques.pdf")

with tab2:
    st.subheader("Diagrama de Flujo de Proceso (PFD)")
    mostrar_pdf("diagrama_flujo.pdf")
