import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai

# Configuración inicial de la página
st.set_page_config(page_title="Simulador BioSTEAM & Gestión ISO", layout="wide")

# ===============================================
# 1. LÓGICA DE SIMULACIÓN
# ===============================================
def run_simulation(params):
    bst.main_flowsheet.clear()
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    bst.settings.electricity_price = params['p_luz']
    
    mosto = bst.Stream("1_MOSTO", 
                       Water=43.2, Ethanol=4.9, units="kmol/h",
                       T=params['t_mosto'] + 273.15, P=101325)
    mosto.price = params['p_mosto_in']

    vinazas_retorno = bst.Stream("Vinazas_Retorno", Water=43.335, units="kmol/h",
                                 T=90+273.15, P=300000)

    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno), 
                         outs=("3_MOSTO_PRE", "DRENAJE"), phase0="l", phase1="l")
    W210.outs[0].T = 85 + 273.15

    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla", T=params['t_w220_out'] + 273.15)
    
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bif", P=params['p_v100'] * 101325)
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor", "Vinazas"), P=101325, Q=0)
    
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto", T=25+273.15)
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    sys = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    sys.simulate()
    
    # Cálculos energéticos seguros
    calor_enfr_kw = 0
    calor_calen_kw = 0
    for u in sys.units:
        for hu in u.heat_utilities:
            if hu.duty is not None:
                if hu.duty < 0: calor_enfr_kw += abs(hu.duty)
                else: calor_calen_kw += hu.duty
    
    # Análisis Económico
    prod = sys.flowsheet.stream.Producto
    costo_servicios = ((calor_enfr_kw/3600) * params['p_agua'] / 1000) + ((calor_calen_kw/3600) * params['p_vapor'] / 1000)
    costo_mp = mosto.F_mass * mosto.price
    costo_total_h = costo_servicios + costo_mp
    
    costo_real = costo_total_h / prod.F_mass if prod.F_mass > 0 else 0
    ventas_h = prod.F_mass * params['p_etanol_vta']
    flujo_anual = (ventas_h - costo_total_h) * 8000
    
    inv = 500000
    roi = (flujo_anual / inv) * 100
    pb = inv / flujo_anual if flujo_anual > 0 else 0
    npv = -inv + (flujo_anual / 0.1)

    return sys, prod, {"ROI": roi, "Payback": pb, "NPV": npv, "Costo_Real": costo_real}

# ===============================================
# 2. INTERFAZ DE USUARIO
# ===============================================
st.title("🏭 Planta de Procesos BioSTEAM - Ingeniería de Alimentos")

with st.sidebar:
    st.header("⚙️ Variables de Operación")
    t_mosto = st.slider("Temp. Alimentación (°C)", 10, 50, 25)
    t_w220 = st.slider("Temp. Salida W220 (°C)", 70, 110, 95)
    p_v100 = st.slider("Presión V100 (atm)", 0.5, 5.0, 1.0)
    
    st.header("💲 Precios de Mercado")
    p_luz = st.number_input("Precio Electricidad ($/kWh)", value=0.15)
    p_vapor = st.number_input("Precio Vapor ($/ton)", value=20.0)
    p_agua = st.number_input("Precio Agua ($/ton)", value=1.5)
    p_mosto = st.number_input("Costo Mosto ($/kg)", value=0.5)
    p_etanol = st.number_input("Precio Venta Etanol ($/kg)", value=2.5)
    
    st.markdown("---")
    tutor_ia = st.toggle("Habilitar Tutor IA")
    ejecutar = st.button("🚀 EJECUTAR SIMULACIÓN", use_container_width=True)

params = {
    't_mosto': t_mosto, 't_w220_out': t_w220, 'p_v100': p_v100,
    'p_luz': p_luz, 'p_vapor': p_vapor, 'p_agua': p_agua,
    'p_mosto_in': p_mosto, 'p_etanol_vta': p_etanol
}

if ejecutar:
    sys, producto, econ = run_simulation(params)

    # 10. RECUADROS DE PRODUCTO FINAL E INDICADORES
    st.subheader("📍 Producto Final y Viabilidad Económica")
    
    # Fila 1: Variables Físicas
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Presión", f"{producto.P / 101325:.2f} atm")
    r2.metric("Temperatura", f"{producto.T - 273.15:.2f} °C")
    r3.metric("Flujo Másico", f"{producto.F_mass:.2f} kg/h")
    comp = (producto.imass['Ethanol']/producto.F_mass)*100 if producto.F_mass > 0 else 0
    r4.metric("Composición", f"{comp:.1f} % Etanol")

    # Fila 2: Variables Económicas
    e1, e2, e3, e4, e5 = st.columns(5)
    e1.metric("Costo Real", f"${econ['Costo_Real']:.2f}/kg", delta_color="inverse")
    e2.metric("Venta Sugerida", f"${econ['Costo_Real']*1.35:.2f}/kg")
    e3.metric("NPV", f"${econ['NPV']:,.0f}")
    e4.metric("Payback", f"{econ['Payback']:.2f} años")
    e5.metric("ROI", f"{econ['ROI']:.1f} %")

    # 9. TABLAS DE BALANCE
    st.markdown("---")
    col_izq, col_der = st.columns(2)
    with col_izq:
        st.subheader("📊 Balance de Materia")
        st.dataframe(pd.DataFrame([{ "Stream": s.ID, "kg/h": round(s.F_mass,2)} for s in sys.streams if s.F_mass > 0]), use_container_width=True)
    with col_der:
        st.subheader("⚡ Balance de Energía")
        st.dataframe(pd.DataFrame([{ "Unit": u.ID, "kW": round(sum([h.duty for h in u.heat_utilities])/3600,2) if u.heat_utilities else 0} for u in sys.units]), use_container_width=True)

    # 11 y 12. DIAGRAMAS ISO (AUTOCAD PLANT 3D)
    st.markdown("---")
    st.subheader("📜 Documentación Técnica Estándar ISO")
    d1, d2 = st.columns(2)
    
    with d1:
        st.info("📂 **Diagrama de Bloques (ISO)**")
        st.caption("Generado desde AutoCAD Plant 3D")
        # Aquí debes colocar el nombre exacto de tus archivos PDF que subas a GitHub
        with open("diagrama_bloques.pdf", "rb") as f:
            st.download_button("Descargar Diagrama de Bloques", f, file_name="Diagrama_Bloques_ISO.pdf")
            
    with d2:
        st.info("📂 **Diagrama de Flujo de Proceso (ISO)**")
        st.caption("Avance AutoCAD Plant 3D")
        with open("diagrama_flujo.pdf", "rb") as f:
            st.download_button("Descargar PFD ISO", f, file_name="PFD_ISO_Actualizado.pdf")

    # 13, 14, 15. TUTOR IA Y CHAT
    if tutor_ia:
        st.divider()
        st.subheader("🤖 Ventana de Contexto: Tutor IA")
        if "GEMINI_API_KEY" in st.secrets:
            genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
            model = genai.GenerativeModel('gemini-1.5-flash')
            
            if "messages" not in st.session_state: st.session_state.messages = []
            for m in st.session_state.messages:
                with st.chat_message(m["role"]): st.markdown(m["content"])

            if prompt := st.chat_input("Dime, ¿qué parte del balance no te queda clara?"):
                st.session_state.messages.append({"role": "user", "content": prompt})
                with st.chat_message("user"): st.markdown(prompt)
                
                resp = model.generate_content(f"Contexto: ROI {econ['ROI']}%, Pureza {comp}%. Usuario: {prompt}")
                with st.chat_message("assistant"):
                    st.markdown(resp.text)
                    st.session_state.messages.append({"role": "assistant", "content": resp.text})
        else:
            st.error("Configura la API Key en Secrets.")

else:
    st.info("Ajusta los parámetros y presiona el botón para visualizar el layout y los indicadores.")
