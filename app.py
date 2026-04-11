import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai

# Configuración de página
st.set_page_config(page_title="BioSTEAM Explorer", layout="wide")

# ===============================================
# LÓGICA DE SIMULACIÓN (ENCAPSULADA)
# ===============================================
def run_simulation(f_etanol, f_agua, temp_c):
    # Limpieza de flowsheet para evitar errores de ID duplicado en cada rerun
    bst.main_flowsheet.clear()
    
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # Corrientes dinámicas
    mosto = bst.Stream("1-MOSTO",
                       Water=f_agua, Ethanol=f_etanol, units="kmol/h",
                       T=temp_c + 273.15, P=101325)

    vinazas_retorno = bst.Stream("Vinazas-Retorno", Water=f_agua, units="kmol/h",
                                 T=90+273.15, P=300000)

    # Definición de Equipos
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno),
                         outs=("3-MOSTO-PRE", "DRENAJE"), phase0="l", phase1="l")
    W210.outs[0].T = 85 + 273.15

    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla", T=95+273.15)
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla-Bif", P=101325)
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor", "Vinazas"), P=101325, Q=0)
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto", T=25+273.15)
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    sys = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    sys.simulate()
    return sys

def generar_tablas(sistema):
    # Tabla de Materia
    datos_mat = []
    for s in sistema.streams:
        if s.F_mass > 0:
            datos_mat.append({
                "ID": s.ID,
                "Temp (°C)": round(s.T - 273.15, 2),
                "Flujo (kg/h)": round(s.F_mass, 2),
                "% Etanol": round((s.imass['Ethanol']/s.F_mass)*100, 1) if s.F_mass > 0 else 0
            })
    df_mat = pd.DataFrame(datos_mat)

    # Tabla de Energía (Manejo de duty para evitar errores en Flash)
    datos_en = []
    for u in sistema.units:
        # Sumamos duty de todas las utilidades de calor del equipo
        calor_kw = sum([hu.duty for hu in u.heat_utilities]) / 3600
        potencia_kw = u.power_utility.rate if u.power_utility else 0
        
        if abs(calor_kw) > 0.001 or potencia_kw > 0.001:
            datos_en.append({
                "Equipo": u.ID,
                "Calor (kW)": round(calor_kw, 2),
                "Potencia (kW)": round(potencia_kw, 2)
            })
    df_en = pd.DataFrame(datos_en)
    return df_mat, df_en

# ===============================================
# INTERFAZ DE USUARIO (STREAMLIT)
# ===============================================
st.title("🏭 Simulador de Procesos: Ingeniería de Alimentos")
st.markdown("---")

# Sidebar para parámetros
with st.sidebar:
    st.header("⚙️ Parámetros de Operación")
    f_et = st.slider("Flujo Etanol (kmol/h)", 1.0, 15.0, 4.9)
    f_ag = st.slider("Flujo Agua (kmol/h)", 10.0, 100.0, 43.2)
    t_in = st.number_input("Temperatura Entrada (°C)", value=25)
    
    st.markdown("---")
    simular = st.button("🚀 Ejecutar Simulación", use_container_width=True)

if simular:
    try:
        with st.spinner('Calculando balances...'):
            sistema = run_simulation(f_et, f_ag, t_in)
            df_mat, df_en = generar_tablas(sistema)
            
            # 1. MÉTRICAS PERSONALIZADAS
            # Extraemos datos específicos para los KPI
            prod_final = sistema.flowsheet.stream.Producto
            pureza = (prod_final.imass['Ethanol'] / prod_final.F_mass) * 100
            energia_total = df_en["Calor (kW)"].abs().sum()

            m1, m2, m3 = st.columns(3)
            m1.metric("Producción Total", f"{prod_final.F_mass:.2f} kg/h", delta="Salida")
            m2.metric("Pureza de Etanol", f"{pureza:.1f}%", delta="Destilado")
            m3.metric("Carga Térmica Total", f"{energia_total:.2f} kW", delta_color="inverse")

            st.markdown("---")

            # 2. LAYOUT DE COLUMNAS PARA TABLAS
            col_izq, col_der = st.columns(2)

            with col_izq:
                st.subheader("📊 Balance de Materia")
                st.dataframe(df_mat, use_container_width=True, hide_index=True)

            with col_der:
                st.subheader("⚡ Balance de Energía")
                st.dataframe(df_en, use_container_width=True, hide_index=True)

            # 3. DIAGRAMA DE FLUJO
            st.markdown("---")
            st.subheader("🖼️ Diagrama de Flujo (PFD)")
            dot = sistema.diagram(format='dot', display=False)
            st.graphviz_chart(dot)

            # 4. INTEGRACIÓN IA (OPCIONAL)
            st.subheader("🧠 Análisis Asistido por IA")
            try:
                genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
                modelo = genai.GenerativeModel("gemini-2.5-pro")
                
                prompt = f"""
                Actúa como un ingeniero químico senior evaluando el reporte técnico de una simulación de separación de etanol.
                
                Tabla de Materia:
                {df_materia.to_markdown(index=False)}
                
                Tabla de Energía:
                {df_energia.to_markdown(index=False)}
                
                Proporciona un reporte de 3 párrafos concisos:
                1. Evalúa la viabilidad técnica del proceso.
                2. Señala el equipo con mayor consumo energético y su impacto.
                3. Proporciona una sugerencia técnica directa para optimización termodinámica.
                """
                respuesta = modelo.generate_content(prompt)
                st.success(respuesta.text)
            except Exception as e:
                st.warning("⚠️ La conexión con la API de Gemini no está configurada correctamente en los Secrets.")

    except Exception as e:
        st.error(f"Error en la simulación: {e}")
else:
    st.info("Ajusta los parámetros en la barra lateral y presiona 'Ejecutar Simulación' para comenzar.")
