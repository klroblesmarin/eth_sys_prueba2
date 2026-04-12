import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai

# Configuración inicial de la página
st.set_page_config(page_title="Simulador BioSTEAM & Tutor IA", layout="wide")

# ===============================================
# 1. LÓGICA DE SIMULACIÓN Y CÁLCULOS
# ===============================================
def run_simulation(params):
    # Limpiar flujos previos para evitar errores de ID duplicado en Streamlit
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

    # Calentador Auxiliar (Dejamos que BioSTEAM asigne el servicio por defecto durante la simulación)
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla", T=params['t_w220_out'] + 273.15)
    
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bif", P=params['p_v100'] * 101325)
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor", "Vinazas"), P=101325, Q=0)
    
    # Condensador (Dejamos que BioSTEAM asigne el servicio por defecto durante la simulación)
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto", T=25+273.15)

    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # Definir y correr Sistema
    sys = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    
    # Al ejecutar esto, BioSTEAM genera las listas de heat_utilities automáticamente
    sys.simulate()
    
    # Análisis Económico
    prod = sys.flowsheet.stream.Producto
    prod.price = params['p_etanol_vta']
    
    # Métodos globales seguros para extraer la energía de toda la planta sin tocar índices [0]
    # BioSTEAM reporta en kJ/hr. Dividimos entre 3600 para pasar a kW.
    calor_enfriamiento_kw = abs(sys.get_cooling_duty()) / 3600
    calor_calentamiento_kw = sys.get_heating_duty() / 3600
    
    # Cálculo de costos por hora (suponiendo que el precio es por tonelada/kWh)
    costo_servicios = (calor_enfriamiento_kw * params['p_agua'] / 1000) + (calor_calentamiento_kw * params['p_vapor'] / 1000)
    ventas_por_hora = prod.F_mass * prod.price
    costo_materia_prima = mosto.F_mass * mosto.price
    
    inversion_inicial = 500000 # Parámetro didáctico
    flujo_caja_horario = ventas_por_hora - costo_servicios - costo_materia_prima
    flujo_caja_anual = flujo_caja_horario * 8000 # asumiendo 8000 horas de operación al año
    
    roi = (flujo_caja_anual / inversion_inicial) * 100 if inversion_inicial > 0 else 0
    payback = inversion_inicial / flujo_caja_anual if flujo_caja_anual > 0 else float('inf')
    npv = -inversion_inicial + (flujo_caja_anual / 0.1) # Perpetuidad simplificada al 10%
    
    costo_real_produccion = (costo_servicios + costo_materia_prima) / prod.F_mass if prod.F_mass > 0 else 0

    return sys, prod, {"ROI": roi, "Payback": payback, "NPV": npv, "Costo_Real": costo_real_produccion}

# ===============================================
# 2. INTERFAZ STREAMLIT
# ===============================================
st.title("👨‍🔬 Simulador Planta de Etanol & Tutor IA")

with st.sidebar:
    st.header("🎮 Controles de Proceso")
    t_mosto = st.slider("Temp. Alimentación Mosto (°C)", 10, 50, 25)
    t_w220 = st.slider("Temp. Salida Calentador W220 (°C)", 70, 110, 95)
    p_v100 = st.slider("Presión Flash V100 (atm)", 0.5, 5.0, 1.0)
    
    st.header("💰 Parámetros Económicos")
    p_luz = st.slider("Precio Electricidad ($/kWh)", 0.05, 0.50, 0.15)
    p_vapor = st.slider("Precio Vapor ($/ton)", 10.0, 50.0, 20.0)
    p_agua = st.slider("Precio Agua Enfriamiento ($/ton)", 0.5, 5.0, 1.5)
    p_mosto_in = st.slider("Precio Compra Mosto ($/kg)", 0.1, 2.0, 0.5)
    p_etanol_vta = st.slider("Precio Venta Etanol ($/kg)", 1.0, 5.0, 2.5)
    
    st.markdown("---")
    tutor_ia = st.toggle("🤖 Habilitar Modo Tutor IA")

# Recopilar parámetros
params = {
    't_mosto': t_mosto, 't_w220_out': t_w220, 'p_v100': p_v100,
    'p_luz': p_luz, 'p_vapor': p_vapor, 'p_agua': p_agua,
    'p_mosto_in': p_mosto_in, 'p_etanol_vta': p_etanol_vta
}

# Ejecutar simulación con manejo de errores global
try:
    sys, producto, econ = run_simulation(params)
    simulacion_exitosa = True
except Exception as e:
    st.error(f"Error en la simulación: {e}. Ajusta los parámetros del proceso.")
    simulacion_exitosa = False

if simulacion_exitosa:
    # ===============================================
    # 3. DASHBOARD DE MÉTRICAS (PRODUCTO FINAL)
    # ===============================================
    st.subheader("📦 Corriente de Producto Final")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Temperatura", f"{producto.T - 273.15:.2f} °C")
    c2.metric("Presión", f"{producto.P / 101325:.2f} atm")
    c3.metric("Flujo Másico", f"{producto.F_mass:.2f} kg/h")
    
    pureza_etanol = (producto.imass['Ethanol']/producto.F_mass)*100 if producto.F_mass > 0 else 0
    c4.metric("Composición Etanol", f"{pureza_etanol:.1f} %")
    
    st.subheader("📈 Análisis Financiero")
    e1, e2, e3, e4 = st.columns(4)
    e1.metric("Costo Real Producción", f"${econ['Costo_Real']:.2f} /kg")
    e2.metric("Precio Venta Sugerido", f"${econ['Costo_Real'] * 1.30:.2f} /kg") 
    e3.metric("Payback", f"{econ['Payback']:.2f} años" if econ['Payback'] != float('inf') else "No rentable")
    e4.metric("ROI Anual", f"{econ['ROI']:.1f} %")
    st.caption(f"**NPV Estimado (10% de descuento):** ${econ['NPV']:,.2f}")
    
    # ===============================================
    # 4. TABLAS DE BALANCE
    # ===============================================
    st.markdown("---")
    col_mat, col_en = st.columns(2)
    
    with col_mat:
        st.subheader("📊 Balance de Materia")
        datos_mat = []
        for s in sys.streams:
            if s.F_mass > 0:
                datos_mat.append({
                    "ID": s.ID, 
                    "Flujo (kg/h)": round(s.F_mass,2), 
                    "T (°C)": round(s.T-273.15,1),
                    "P (atm)": round(s.P/101325, 2)
                })
        st.dataframe(pd.DataFrame(datos_mat), use_container_width=True)
    
    with col_en:
        st.subheader("⚡ Balance de Energía")
        datos_en = []
        for u in sys.units:
            # Validación segura de duty después de la simulación
            calor = sum([h.duty for h in u.heat_utilities])/3600 if hasattr(u, 'heat_utilities') and u.heat_utilities else 0
            potencia = u.power_utility.rate if hasattr(u, 'power_utility') and u.power_utility else 0
            if abs(calor) > 0.01 or potencia > 0.01:
                datos_en.append({
                    "Equipo": u.ID, 
                    "Calor Transferido (kW)": round(calor,2),
                    "Trabajo/Eléctrica (kW)": round(potencia,2)
                })
        st.dataframe(pd.DataFrame(datos_en), use_container_width=True)
    
    # ===============================================
    # 5. DIAGRAMA DE FLUJO
    # ===============================================
    with st.expander("Ver Diagrama de Flujo del Proceso (PFD)"):
        dot = sys.diagram(format='dot', display=False)
        st.graphviz_chart(dot)
    
    # ===============================================
    # 6. TUTOR IA (GEMINI)
    # ===============================================
    if tutor_ia:
        st.markdown("---")
        st.subheader("🤖 Tutor de Ingeniería Química (Gemini)")
        
        if "GEMINI_API_KEY" in st.secrets:
            genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
            model = genai.GenerativeModel('gemini-1.5-pro')
            
            if "messages" not in st.session_state:
                st.session_state.messages = []
    
            for message in st.session_state.messages:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])
    
            if prompt := st.chat_input("Pregúntame sobre los balances, termodinámica o finanzas del proceso..."):
                st.session_state.messages.append({"role": "user", "content": prompt})
                with st.chat_message("user"):
                    st.markdown(prompt)
    
                contexto = f"""
                Eres un tutor de ingeniería química explicando una simulación de BioSTEAM a un estudiante.
                El proceso es una separación de Etanol y Agua.
                Variables actuales: Temperatura de alimentación: {params['t_mosto']}°C. Presión flash: {params['p_v100']} atm.
                Resultados actuales: Flujo de producto: {producto.F_mass:.2f} kg/h, Pureza de Etanol: {pureza_etanol:.1f}%.
                Indicadores económicos: ROI {econ['ROI']:.1f}%, Costo Real de Producción ${econ['Costo_Real']:.2f}/kg.
                
                Pregunta del estudiante: {prompt}
                
                Responde de forma clara, didáctica y basada en estos datos. Usa markdown para resaltar conceptos clave.
                """
                
                with st.chat_message("assistant"):
                    try:
                        with st.spinner("Analizando proceso..."):
                            response = model.generate_content(contexto)
                            st.markdown(response.text)
                            st.session_state.messages.append({"role": "assistant", "content": response.text})
                    except Exception as e:
                        st.error(f"Error al conectar con la IA: {e}")
        else:
            st.warning("⚠️ El tutor está activado, pero falta la clave de la API. Ve a los *Secrets* de Streamlit Cloud y añade `GEMINI_API_KEY = 'tu_clave'`.")
