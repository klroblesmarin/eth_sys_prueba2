import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai

# Configuración inicial de la página
st.set_page_config(page_title="BioSTEAM Engineering Tutor", layout="wide")

# ===============================================
# 1. LÓGICA DE SIMULACIÓN Y CÁLCULOS
# ===============================================
def run_simulation(params):
    # Limpiar flujos previos para evitar errores de ID duplicado
    bst.main_flowsheet.clear()
    
    # Configuración de Químicos
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # Precios de Servicios y Productos (Configuración BioSTEAM)
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

    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla", T=params['t_w220_out'] + 273.15)
    # Configurar precio de vapor en la utilidad del W220
    W220.heat_utilities[0].agent = bst.HeatUtility.get_agent('low_pressure_steam')
    
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla_Bif", P=params['p_v100'] * 101325)
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor", "Vinazas"), P=101325, Q=0)
    
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto", T=25+273.15)
    W310.heat_utilities[0].agent = bst.HeatUtility.get_agent('cooling_water')

    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # Definir Sistema
    sys = bst.System("planta_etanol", path=(P100, W210, W220, V100, V1, W310, P200))
    sys.simulate()
    
    # Análisis Económico Simple (Simulado para fines educativos)
    prod = sys.flowsheet.stream.Producto
    prod.price = params['p_etanol_vta']
    
    costo_op = sys.get_cooling_duty() * params['p_agua'] + sys.get_heating_duty() * params['p_vapor']
    ventas = prod.F_mass * prod.price
    inversion_inicial = 500000 # Valor base de ejemplo
    flujo_caja = ventas - costo_op - (mosto.F_mass * mosto.price)
    
    roi = (flujo_caja / inversion_inicial) * 100
    payback = inversion_inicial / flujo_caja if flujo_caja > 0 else float('inf')
    npv = -inversion_inicial + (flujo_caja / 0.1) # Perpetuidad simplificada al 10%
    
    return sys, prod, {"ROI": roi, "Payback": payback, "NPV": npv, "Costo_Real": costo_op/prod.F_mass if prod.F_mass > 0 else 0}

# ===============================================
# 2. INTERFAZ STREAMLIT
# ===============================================
st.title("👨‍🔬 BioSTEAM Intelligent Interface & IA Tutor")

with st.sidebar:
    st.header("🎮 Controles de Proceso")
    t_mosto = st.slider("Temp. Alimentación Mosto (°C)", 10, 50, 25)
    t_w220 = st.slider("Temp. Salida W220 (°C)", 70, 110, 95)
    p_v100 = st.slider("Presión V100 (atm)", 0.5, 5.0, 1.0)
    
    st.header("💰 Parámetros Económicos")
    p_luz = st.slider("Precio Electricidad ($/kWh)", 0.05, 0.5, 0.15)
    p_vapor = st.slider("Precio Vapor ($/ton)", 10, 50, 20)
    p_agua = st.slider("Precio Agua Enfriamiento ($/ton)", 0.5, 5.0, 1.5)
    p_mosto_in = st.slider("Costo Materia Prima ($/kg)", 0.1, 2.0, 0.5)
    p_etanol_vta = st.slider("Precio Venta Etanol ($/kg)", 1.0, 5.0, 2.5)
    
    st.markdown("---")
    tutor_ia = st.toggle("Habilitar Modo Tutor IA")

# Ejecución de simulación
params = {
    't_mosto': t_mosto, 't_w220_out': t_w220, 'p_v100': p_v100,
    'p_luz': p_luz, 'p_vapor': p_vapor, 'p_agua': p_agua,
    'p_mosto_in': p_mosto_in, 'p_etanol_vta': p_etanol_vta
}

sys, producto, econ = run_simulation(params)

# 3. DASHBOARD DE MÉTRICAS (PRODUCTO FINAL)
st.subheader("📦 Indicadores de Producto Final")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Temperatura", f"{producto.T - 273.15:.2f} °C")
c2.metric("Presión", f"{producto.P / 101325:.2f} atm")
c3.metric("Flujo Másico", f"{producto.F_mass:.2f} kg/h")
c4.metric("Comp. Etanol", f"{(producto.imass['Ethanol']/producto.F_mass)*100:.1f} %")

st.subheader("📈 Análisis Financiero")
e1, e2, e3, e4 = st.columns(4)
e1.metric("Costo Real", f"${econ['Costo_Real']:.2f} /kg")
e2.metric("NPV")
