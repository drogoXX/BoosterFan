#!/usr/bin/env python3
"""
Enhanced Booster Fan Analysis with Performance Curves and Sensitivity Analysis

This script generates:
1. Fan performance curves at various RPM (pressure-flow-efficiency)
2. System operating points overlay with improved system curve model
3. Sensitivity analysis of cost vs. design margin
4. Motor and VFD efficiency curves
5. Comprehensive visualization suite
6. Part-load operating profile analysis
7. Maintenance cost considerations

ENHANCEMENTS (v2.0):
- Added VFD efficiency losses
- Improved system curve model (static + dynamic components)
- Part-load operating profile analysis
- Maintenance costs included in lifecycle analysis
- Consistent pressure calculations across all plots

FIXES (v2.1):
- Use actual fan curves for efficiency calculations (not hardcoded)
- Added division-by-zero protection
- Standardized design margin constant
- Corrected motor efficiency curve shape
- Fixed speed calculation documentation
- Calculated system static pressure from data
- Improved VFD efficiency type handling
- Added operating hours validation
- Removed duplicate calculations

FIXES (v2.2):
- Implemented bilinear interpolation for fan efficiency
- Eliminates discontinuities/peaks in sensitivity analysis
- Smooth interpolation across both flow and RPM dimensions
- More accurate representation of VFD-controlled fan operation

REQUIREMENTS:
    pip install matplotlib numpy --break-system-packages
"""

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
import math
import csv
import os

# Set style for professional plots
plt.style.use('seaborn-v0_8-darkgrid')
plt.rcParams['figure.figsize'] = (16, 10)
plt.rcParams['font.size'] = 10
plt.rcParams['axes.grid'] = True
plt.rcParams['grid.alpha'] = 0.3

# ============================================================================
# INPUT DATA FROM DATASHEET
# ============================================================================

# Operating points from datasheet
flow_design = 177521  # m³/h STP (125% CCLPB)
flow_cclpb = 142017   # m³/h STP (Maximum)
flow_cclpa = 134754   # m³/h STP (Nominal)
flow_cclpe = 100000   # m³/h STP (Minimum)

pressure_design = 103.9  # mbar
pressure_cclpb = 65.8    # mbar
pressure_cclpa = 59.3    # mbar
pressure_cclpe = 31.8    # mbar

efficiency_nominal = 80.0  # % at nominal
max_rpm = 1500  # rpm

# Economic parameters
electricity_cost = 0.30  # €/kWh - UK industrial rate
operating_hours = 8000  # h/year
plant_lifetime = 30  # years
discount_rate = 0.00
electricity_escalation = 0.02
motor_cost_per_kw = 650  # €/kW (includes motor + VFD)
co2_intensity = 0.23  # kg CO2/kWh
maintenance_cost_rate = 0.03  # Annual maintenance as fraction of CAPEX (3%)

# System curve parameters
# Real systems have both static and dynamic pressure components
# P_system = P_static + k * Q^2
# Calculate from known operating points (CCLPA and CCLPB)
# Using two points to solve for P_static and k:
#   P_cclpa = P_static + k * Q_cclpa²
#   P_cclpb = P_static + k * Q_cclpb²
# Solving: k = (P_cclpb - P_cclpa) / (Q_cclpb² - Q_cclpa²)
#          P_static = P_cclpa - k * Q_cclpa²

system_dynamic_coeff = (pressure_cclpb - pressure_cclpa) / (flow_cclpb ** 2 - flow_cclpa ** 2)  # mbar/(m³/h)²
system_static_pressure = pressure_cclpa - system_dynamic_coeff * (flow_cclpa ** 2)  # mbar

# Validate: static pressure should be >= 0 (physical constraint)
if system_static_pressure < 0:
    # If negative, system is purely dynamic (no static component)
    print(f"Warning: Calculated static pressure is negative ({system_static_pressure:.2f} mbar). "
          f"Assuming purely dynamic system (P_static = 0).")
    system_static_pressure = 0.0
    system_dynamic_coeff = pressure_cclpa / (flow_cclpa ** 2)

# Operating profile (part-load distribution)
# Represents typical plant operation: % of time at each load point
operating_profile = {
    'cclpe': {'flow': flow_cclpe, 'pressure': pressure_cclpe, 'hours_per_year': 80},   # 1% of time
    'cclpa': {'flow': flow_cclpa, 'pressure': pressure_cclpa, 'hours_per_year': 7760},  # 97% of time
    'cclpb': {'flow': flow_cclpb, 'pressure': pressure_cclpb, 'hours_per_year': 160},  # 2% of time
}

# Validate operating profile hours
_total_profile_hours = sum(op['hours_per_year'] for op in operating_profile.values())
if abs(_total_profile_hours - operating_hours) > 1:  # Allow 1 hour tolerance for rounding
    raise ValueError(
        f"Operating profile hours ({_total_profile_hours}) do not match "
        f"total operating hours ({operating_hours}). "
        f"Please adjust the profile to sum to {operating_hours} hours/year."
    )

# CURRENT DESIGN MARGIN - Single source of truth
CURRENT_DESIGN_MARGIN_PCT = 31.7  # Current design is 132% of CCLPA (1.317 = 1 + 0.317)

# ============================================================================
# FAN AFFINITY LAWS
# ============================================================================

def fan_affinity_flow(Q_base, rpm_base, rpm_new):
    """Flow varies linearly with RPM"""
    return Q_base * (rpm_new / rpm_base)

def fan_affinity_pressure(P_base, rpm_base, rpm_new):
    """Pressure varies with square of RPM"""
    return P_base * (rpm_new / rpm_base) ** 2

def fan_affinity_power(W_base, rpm_base, rpm_new):
    """Power varies with cube of RPM"""
    return W_base * (rpm_new / rpm_base) ** 3

# ============================================================================
# FAN CHARACTERISTIC CURVES
# ============================================================================

def generate_fan_curve(rpm, base_rpm=1500):
    """
    Generate fan characteristic curve for given RPM using affinity laws
    and typical centrifugal fan polynomial characteristics
    """
    # Base curve at 1500 RPM - polynomial approximation
    # Typical fan curve: ΔP = a - b*Q² (shutoff head to free delivery)

    # Use design point as reference (at 100% RPM)
    Q_ref = flow_design
    P_ref = pressure_design

    # Generate curve points (0 to 120% of design flow)
    Q_range = np.linspace(0, Q_ref * 1.2, 100)

    # Polynomial fan curve: P = P_shutoff - k*Q²
    # At design point: P_ref = P_shutoff - k*Q_ref²
    # At free delivery: P = 0, Q = Q_max
    # Assume Q_max = 1.3 * Q_ref (typical for centrifugal fans)
    Q_max_base = Q_ref * 1.3

    # Calculate coefficients with protection against division by zero
    ratio_squared = (Q_ref / Q_max_base) ** 2
    if ratio_squared >= 0.99:  # Protection: if Q_ref too close to Q_max
        raise ValueError(f"Q_ref ({Q_ref}) too close to Q_max ({Q_max_base}). Adjust Q_max multiplier.")

    P_shutoff_base = P_ref / (1 - ratio_squared)
    k = P_shutoff_base / (Q_max_base ** 2)

    # Base pressure curve at reference RPM
    P_base = P_shutoff_base - k * Q_range ** 2
    P_base[P_base < 0] = 0  # No negative pressure

    # Apply affinity laws for actual RPM
    rpm_ratio = rpm / base_rpm
    Q_actual = Q_range * rpm_ratio
    P_actual = P_base * (rpm_ratio ** 2)

    # Efficiency curve (parabolic, peaks around 70% of Q_max)
    Q_peak_eff = Q_ref * 0.95  # Peak efficiency near design point
    eff_peak = 85.0  # Peak efficiency

    # Gaussian-like efficiency curve
    efficiency = eff_peak * np.exp(-((Q_range - Q_peak_eff) ** 2) / (2 * (Q_peak_eff * 0.4) ** 2))
    efficiency = efficiency * rpm_ratio ** 0.1  # Slight efficiency loss at lower RPM
    efficiency = np.clip(efficiency, 0, 90)  # Cap at realistic values

    return Q_actual, P_actual, efficiency


def estimate_rpm_from_operating_point(flow_m3h, pressure_mbar, base_rpm=1500):
    """
    Estimate the RPM required to achieve a given flow and pressure
    using affinity laws and the base fan curve

    Args:
        flow_m3h: Target flow rate in m³/h
        pressure_mbar: Target pressure rise in mbar
        base_rpm: Base RPM for reference curve

    Returns:
        Estimated RPM required
    """
    # Use affinity laws: P ∝ (N/N_ref)² and Q ∝ (N/N_ref)
    # At base RPM, get the pressure at this flow rate
    Q_base, P_base, _ = generate_fan_curve(base_rpm)

    # Interpolate pressure at target flow on base curve
    if flow_m3h < Q_base[0]:
        pressure_at_base_rpm = P_base[0]
    elif flow_m3h > Q_base[-1]:
        pressure_at_base_rpm = P_base[-1]
    else:
        pressure_at_base_rpm = np.interp(flow_m3h, Q_base, P_base)

    # Affinity law: P_target / P_base = (N_target / N_base)²
    # Therefore: N_target = N_base * sqrt(P_target / P_base)
    if pressure_at_base_rpm > 0:
        rpm_ratio = np.sqrt(pressure_mbar / pressure_at_base_rpm)
        rpm_estimated = base_rpm * rpm_ratio
    else:
        rpm_estimated = base_rpm

    # Ensure RPM is within reasonable bounds
    rpm_estimated = np.clip(rpm_estimated, 800, 1600)

    return rpm_estimated


def get_fan_efficiency_at_operating_point(flow_m3h, pressure_mbar):
    """
    Smoothly interpolate fan efficiency at a given operating point (flow, pressure)
    using bilinear interpolation across flow and RPM dimensions.

    This eliminates discontinuities caused by discrete RPM curve selection.

    Args:
        flow_m3h: Volumetric flow rate in m³/h
        pressure_mbar: Pressure rise in mbar

    Returns:
        Interpolated fan efficiency in % (0-100)
    """
    # Step 1: Estimate the RPM required for this operating point
    rpm_required = estimate_rpm_from_operating_point(flow_m3h, pressure_mbar)

    # Step 2: Define a finer RPM grid for interpolation
    rpm_grid = np.arange(1000, 1600, 100)  # Every 100 RPM for smooth interpolation

    # Find bounding RPMs
    rpm_lower_idx = np.searchsorted(rpm_grid, rpm_required) - 1
    rpm_lower_idx = max(0, min(rpm_lower_idx, len(rpm_grid) - 2))

    rpm_lower = rpm_grid[rpm_lower_idx]
    rpm_upper = rpm_grid[rpm_lower_idx + 1]

    # Step 3: Generate curves at both bounding RPMs
    Q_lower, P_lower, eff_lower = generate_fan_curve(rpm_lower)
    Q_upper, P_upper, eff_upper = generate_fan_curve(rpm_upper)

    # Step 4: Interpolate efficiency at target flow on both curves
    if flow_m3h < Q_lower[0]:
        eff_at_lower_rpm = eff_lower[0]
    elif flow_m3h > Q_lower[-1]:
        eff_at_lower_rpm = eff_lower[-1]
    else:
        eff_at_lower_rpm = np.interp(flow_m3h, Q_lower, eff_lower)

    if flow_m3h < Q_upper[0]:
        eff_at_upper_rpm = eff_upper[0]
    elif flow_m3h > Q_upper[-1]:
        eff_at_upper_rpm = eff_upper[-1]
    else:
        eff_at_upper_rpm = np.interp(flow_m3h, Q_upper, eff_upper)

    # Step 5: Interpolate between the two RPM curves
    if rpm_lower == rpm_upper or abs(rpm_required - rpm_lower) < 1:
        efficiency_pct = eff_at_lower_rpm
    else:
        # Linear interpolation between RPMs
        weight_upper = (rpm_required - rpm_lower) / (rpm_upper - rpm_lower)
        weight_lower = 1 - weight_upper
        efficiency_pct = weight_lower * eff_at_lower_rpm + weight_upper * eff_at_upper_rpm

    # Ensure realistic range
    efficiency_pct = np.clip(efficiency_pct, 30, 90)

    return float(efficiency_pct)


# ============================================================================
# MOTOR EFFICIENCY CURVE
# ============================================================================

def motor_efficiency_curve(load_percent):
    """
    Calculate motor efficiency as function of load percentage
    Based on typical large induction motor efficiency curves (IE3/IE4 class)

    Peak efficiency occurs around 75-85% load, then plateaus or slightly decreases.
    Efficiency drops significantly below 50% load.
    """
    # Convert to array if single value
    load = np.atleast_1d(load_percent)

    # Typical efficiency curve (peaks around 75-85% load)
    eff = np.zeros_like(load)

    for i, L in enumerate(load):
        if L < 25:
            # Very poor efficiency at low loads (25% load = ~78%)
            eff[i] = 0.60 + 0.0072 * L
        elif L < 50:
            # Improving efficiency (50% load = ~90%)
            eff[i] = 0.78 + 0.0048 * (L - 25)
        elif L < 75:
            # Approaching peak (75% load = ~95%)
            eff[i] = 0.90 + 0.0020 * (L - 50)
        elif L <= 100:
            # Peak efficiency region - plateau at ~95-96%
            # Efficiency plateaus, doesn't continue rising
            eff[i] = 0.95 + 0.0004 * (L - 75)
        else:
            # Overload region (efficiency decreases)
            eff[i] = max(0.96 - 0.001 * (L - 100), 0.85)

    return eff if isinstance(load_percent, np.ndarray) else eff[0]

def vfd_efficiency_curve(load_percent, speed_percent=None):
    """
    Calculate VFD efficiency as function of load percentage
    VFD efficiency typically: 97-98% at full load, drops at part load
    If speed_percent is provided, accounts for speed-dependent losses

    Args:
        load_percent: Motor load as percentage (0-100+) - scalar, list, or numpy array
        speed_percent: Speed as fraction (0-1.0), optional. If None, estimated from load.

    Returns:
        VFD efficiency as fraction (0.90-0.98) - same type as input
    """
    # Store original type for return
    input_is_scalar = isinstance(load_percent, (int, float, np.number))

    # Convert to numpy array for vectorized operations
    load = np.atleast_1d(np.asarray(load_percent, dtype=float))

    if speed_percent is None:
        # Estimate speed from load (assuming cubic relationship: P ∝ N³)
        speed_percent = (load / 100) ** (1/3)
    else:
        speed_percent = np.atleast_1d(np.asarray(speed_percent, dtype=float))

    # Ensure speed_percent is in 0-1 range (convert from % if needed)
    if np.any(speed_percent > 1.5):
        speed_percent = speed_percent / 100

    # Base VFD efficiency (full load, full speed)
    eff_base = 0.98

    # Efficiency drops with speed (due to switching losses)
    # At 50% speed: ~95% efficiency, at 25% speed: ~92% efficiency
    speed_factor = 0.98 - 0.00012 * (100 - speed_percent * 100)

    # Efficiency also drops slightly at very low loads (<30%)
    load_factor = np.ones_like(load)
    load_factor[load < 30] = 0.99 - 0.003 * (30 - load[load < 30])

    eff = eff_base * speed_factor * load_factor
    eff = np.clip(eff, 0.90, 0.98)  # Realistic range

    # Return same type as input
    if input_is_scalar:
        return float(eff[0])
    else:
        return eff

def calculate_system_pressure(flow_m3h, flow_ref=None, pressure_ref=None):
    """
    Calculate system pressure at given flow rate using improved system curve
    P_system = P_static + k * Q^2
    If flow_ref and pressure_ref are provided, calculates from reference point
    Otherwise uses global system parameters
    """
    if flow_ref is None:
        flow_ref = flow_cclpa
    if pressure_ref is None:
        pressure_ref = pressure_cclpa
    
    # Calculate dynamic coefficient from reference point
    k_dynamic = (pressure_ref - system_static_pressure) / (flow_ref ** 2)
    
    # Calculate pressure at requested flow
    pressure = system_static_pressure + k_dynamic * (flow_m3h ** 2)
    
    return max(pressure, 0)  # No negative pressure

# ============================================================================
# COST CALCULATION FUNCTIONS
# ============================================================================

def calculate_fan_power(flow_m3h, pressure_mbar, efficiency_pct):
    """Calculate fan shaft power in kW"""
    flow_m3s = flow_m3h / 3600
    pressure_pa = pressure_mbar * 100
    efficiency_frac = efficiency_pct / 100
    power_kw = (flow_m3s * pressure_pa) / (efficiency_frac * 1000)
    return power_kw

def calculate_lifecycle_cost(design_margin_pct, verbose=False, use_part_load_profile=True):
    """
    Calculate total lifecycle cost for a given design margin
    design_margin_pct: percentage above nominal (e.g., 15 for API 560)
    use_part_load_profile: if True, uses operating profile; if False, assumes 100% at nominal
    Returns: dict with all cost components
    """
    # Design flow based on margin over nominal
    flow_design_case = flow_cclpa * (1 + design_margin_pct / 100)

    # Estimate pressure rise using improved system curve
    pressure_design_case = calculate_system_pressure(flow_design_case, flow_cclpa, pressure_cclpa)

    # Get efficiency from actual fan curves at design point
    eff_design = get_fan_efficiency_at_operating_point(flow_design_case, pressure_design_case)

    # Fan power at design point
    power_fan_design = calculate_fan_power(flow_design_case, pressure_design_case, eff_design)
    
    # Motor sizing
    motor_margin = 1.10
    motor_rated = power_fan_design * motor_margin
    
    # CAPEX (motor and VFD cost)
    capex = motor_rated * motor_cost_per_kw
    
    # Calculate energy consumption based on operating profile
    if use_part_load_profile:
        annual_energy_kwh = 0
        annual_opex_energy = 0
        # Store motor load data to avoid duplicate calculations later
        motor_load_data = []

        for op_point in operating_profile.values():
            flow_op = op_point['flow']
            pressure_op = op_point['pressure']
            hours = op_point['hours_per_year']

            # Get fan efficiency from actual fan curves at this operating point
            eff_op = get_fan_efficiency_at_operating_point(flow_op, pressure_op)

            # Fan power at this operating point
            power_fan_op = calculate_fan_power(flow_op, pressure_op, eff_op)

            # Motor load percentage
            motor_load_pct_op = (power_fan_op / motor_rated) * 100

            # Store for weighted average calculation (avoid duplicate calculation)
            motor_load_data.append({
                'motor_load_pct': motor_load_pct_op,
                'hours': hours
            })

            # Motor efficiency
            motor_eff_op = motor_efficiency_curve(motor_load_pct_op)

            # Estimate speed percentage from flow ratio
            # Per affinity laws: Flow varies linearly with speed (Q ∝ N)
            # Therefore: N/N_design = Q/Q_design
            speed_pct_op = (flow_op / flow_design_case) * 100

            # VFD efficiency
            vfd_eff_op = vfd_efficiency_curve(motor_load_pct_op, speed_pct_op/100)

            # Total system efficiency (fan × motor × VFD)
            total_eff_op = (eff_op / 100) * motor_eff_op * vfd_eff_op

            # Motor input power
            motor_input_power_op = power_fan_op / (motor_eff_op * vfd_eff_op)

            # Energy consumption at this operating point
            energy_op = motor_input_power_op * hours
            annual_energy_kwh += energy_op

        # Annual operating cost (energy only, maintenance added separately)
        annual_opex_energy = annual_energy_kwh * electricity_cost
        
    else:
        # Original method: assume 100% operation at nominal
        # Get fan efficiency from actual curves at nominal operating point
        eff_nominal = get_fan_efficiency_at_operating_point(flow_cclpa, pressure_cclpa)
        power_fan_nominal = calculate_fan_power(flow_cclpa, pressure_cclpa, eff_nominal)
        motor_load_pct = (power_fan_nominal / motor_rated) * 100
        motor_eff = motor_efficiency_curve(motor_load_pct)
        # Speed ratio: Flow varies linearly with speed (affinity law)
        speed_pct_nominal = (flow_cclpa / flow_design_case) * 100
        vfd_eff = vfd_efficiency_curve(motor_load_pct, speed_pct_nominal/100)
        motor_input_power = power_fan_nominal / (motor_eff * vfd_eff)
        annual_energy_kwh = motor_input_power * operating_hours
        annual_opex_energy = annual_energy_kwh * electricity_cost
        motor_load_pct_op = motor_load_pct
    
    # Maintenance costs (annual)
    annual_maintenance = capex * maintenance_cost_rate
    
    # Total annual OPEX (energy + maintenance)
    annual_opex = annual_opex_energy + annual_maintenance
    
    # NPV of OPEX over lifetime
    npv_opex = 0
    for year in range(1, plant_lifetime + 1):
        # Energy cost escalates
        annual_energy_cost = annual_opex_energy * ((1 + electricity_escalation) ** year)
        # Maintenance cost escalates with inflation (assume 2%)
        annual_maint_cost = annual_maintenance * ((1.02) ** year)
        annual_cost = annual_energy_cost + annual_maint_cost
        discounted_cost = annual_cost / ((1 + discount_rate) ** year)
        npv_opex += discounted_cost
    
    # CO2 emissions
    annual_co2_tons = annual_energy_kwh * co2_intensity / 1000
    
    # Total lifecycle cost
    total_lifecycle_cost = capex + npv_opex
    
    # Calculate average motor load for reporting (weighted by operating hours)
    if use_part_load_profile:
        # Use previously calculated motor load data (no duplicate calculation)
        total_hours = sum(data['hours'] for data in motor_load_data)
        weighted_load = sum(data['motor_load_pct'] * data['hours'] / total_hours
                           for data in motor_load_data)
        avg_motor_load_pct = weighted_load
        avg_motor_eff = motor_efficiency_curve(avg_motor_load_pct)
    else:
        avg_motor_load_pct = motor_load_pct_op
        avg_motor_eff = motor_efficiency_curve(avg_motor_load_pct)
    
    if verbose:
        print(f"\nDesign Margin: {design_margin_pct}%")
        print(f"  Motor Rated: {motor_rated:.1f} kW")
        print(f"  Avg Motor Load: {avg_motor_load_pct:.1f}%")
        print(f"  Avg Motor Efficiency: {avg_motor_eff*100:.1f}%")
        print(f"  Annual Energy: {annual_energy_kwh:,.0f} kWh")
        print(f"  Annual Energy Cost: €{annual_opex_energy:,.0f}")
        print(f"  Annual Maintenance: €{annual_maintenance:,.0f}")
        print(f"  Annual OPEX: €{annual_opex:,.0f}")
        print(f"  CAPEX: €{capex:,.0f}")
        print(f"  NPV OPEX (30yr): €{npv_opex:,.0f}")
        print(f"  Total Lifecycle: €{total_lifecycle_cost:,.0f}")
    
    return {
        'design_margin_pct': design_margin_pct,
        'flow_design': flow_design_case,
        'pressure_design': pressure_design_case,
        'motor_rated_kw': motor_rated,
        'motor_load_pct': avg_motor_load_pct,
        'motor_efficiency': avg_motor_eff,
        'motor_input_kw': annual_energy_kwh / operating_hours,  # Average input power
        'annual_energy_kwh': annual_energy_kwh,
        'annual_opex_energy_eur': annual_opex_energy,
        'annual_maintenance_eur': annual_maintenance,
        'annual_opex_eur': annual_opex,
        'annual_co2_tons': annual_co2_tons,
        'capex_eur': capex,
        'npv_opex_eur': npv_opex,
        'total_lifecycle_cost': total_lifecycle_cost
    }

# ============================================================================
# SENSITIVITY ANALYSIS
# ============================================================================

def sensitivity_analysis(use_part_load_profile=True):
    """Run sensitivity analysis over range of design margins"""
    margins = np.arange(10, 35, 1)  # 10% to 34% in 1% steps
    results = [calculate_lifecycle_cost(m, use_part_load_profile=use_part_load_profile) for m in margins]
    return margins, results

# ============================================================================
# PLOTTING FUNCTIONS
# ============================================================================

def create_comprehensive_plots():
    """Create all visualization plots"""
    
    fig = plt.figure(figsize=(20, 12))
    gs = GridSpec(3, 3, figure=fig, hspace=0.3, wspace=0.3)
    
    # ========================================================================
    # PLOT 1: Fan Performance Curves at Various RPM
    # ========================================================================
    ax1 = fig.add_subplot(gs[0, :2])
    
    rpm_values = [1200, 1350, 1500]
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c']
    
    for rpm, color in zip(rpm_values, colors):
        Q, P, eff = generate_fan_curve(rpm)
        ax1.plot(Q/1000, P, color=color, linewidth=2, 
                label=f'{rpm} RPM - Pressure', linestyle='-')
    
    # System curve overlay (for reference)
    system_flows = np.linspace(80, 220, 50) * 1000  # m³/h
    system_pressures = [calculate_system_pressure(q, flow_cclpa, pressure_cclpa) for q in system_flows]
    ax1.plot(system_flows/1000, system_pressures, 'k--', linewidth=2, 
            alpha=0.5, label='System Curve', zorder=3)
    
    # Current Design point
    flow_current = flow_cclpa * (1 + CURRENT_DESIGN_MARGIN_PCT / 100)
    pressure_current = calculate_system_pressure(flow_current, flow_cclpa, pressure_cclpa)
    
    # Plot Current Design point
    ax1.scatter(flow_current/1000, pressure_current, s=200, c='red', 
               marker='X', edgecolors='black', linewidth=2, 
               zorder=5, label='Current Design\n(132% CCLPA)')
    
    # API 560 point (115% of CCLPA) - use consistent system curve
    flow_api = flow_cclpa * 1.15
    pressure_api = calculate_system_pressure(flow_api, flow_cclpa, pressure_cclpa)
    ax1.scatter(flow_api/1000, pressure_api, s=200, c='purple', 
               marker='*', edgecolors='black', linewidth=2, 
               zorder=5, label='API 560\n(115% CCLPA)')
    
    # Plot Nominal point (in legend)
    ax1.scatter(flow_cclpa/1000, pressure_cclpa, s=150, c='green', 
               marker='^', edgecolors='black', linewidth=2, 
               zorder=5, label='Nominal\n(CCLPA)')
    
    # Plot Minimum point (in legend)
    ax1.scatter(flow_cclpe/1000, pressure_cclpe, s=150, c='blue', 
               marker='v', edgecolors='black', linewidth=2, 
               zorder=5, label='Minimum\n(CCLPE)')
    
    # Plot Maximum point (in legend)
    ax1.scatter(flow_cclpb/1000, pressure_cclpb, s=150, c='orange', 
               marker='o', edgecolors='black', linewidth=2, 
               zorder=5, label='Maximum\n(CCLPB)')
    
    # Plot other operating points without labels (for reference only, not in legend)
    ref_points = [
        (flow_design, pressure_design, 'red', 's')
    ]
    
    for flow, press, color, marker in ref_points:
        ax1.scatter(flow/1000, press, s=100, c=color, marker=marker, 
                   edgecolors='black', linewidth=1.5, zorder=4, alpha=0.6)
    
    ax1.set_xlabel('Volumetric Flow Rate (×1000 m³/h)', fontsize=12, fontweight='bold')
    ax1.set_ylabel('Pressure Rise (mbar)', fontsize=12, fontweight='bold')
    ax1.set_title('Fan Performance Curves - Pressure vs Flow at Various RPM', 
                 fontsize=14, fontweight='bold')
    # Legend with RPM curves, Current Design, API 560, Nominal, Minimum, and Maximum
    ax1.legend(loc='upper right', fontsize=9, ncol=2)
    ax1.grid(True, alpha=0.3)
    ax1.set_xlim(50, 220)
    
    # ========================================================================
    # PLOT 2: Fan Efficiency Curves at Various RPM
    # ========================================================================
    ax2 = fig.add_subplot(gs[0, 2])
    
    # Store curves for efficiency interpolation
    curve_data = {}
    for rpm, color in zip(rpm_values, colors):
        Q, P, eff = generate_fan_curve(rpm)
        curve_data[rpm] = (Q, P, eff)
        ax2.plot(Q/1000, eff, color=color, linewidth=2, 
                label=f'{rpm} RPM')
    
    # Helper function to interpolate efficiency at a given flow
    def get_efficiency_at_flow(target_flow, rpm, curve_data):
        """Interpolate efficiency at target flow rate for given RPM"""
        Q, P, eff = curve_data[rpm]
        # Use numpy interp for linear interpolation (Q is already sorted)
        # Clip to valid range
        if target_flow < Q[0]:
            return eff[0]
        if target_flow > Q[-1]:
            return eff[-1]
        return np.interp(target_flow, Q, eff)
    
    # Helper function to find best matching RPM curve for a given flow and pressure
    def find_best_rpm_curve(target_flow, target_pressure, curve_data, rpm_values):
        """Find which RPM curve a point is closest to based on pressure"""
        best_rpm = 1200  # Default
        min_diff = float('inf')
        for rpm in rpm_values:
            Q, P, eff = curve_data[rpm]
            if target_flow >= Q[0] and target_flow <= Q[-1]:
                pressure_at_flow = np.interp(target_flow, Q, P)
                diff = abs(pressure_at_flow - target_pressure)
                if diff < min_diff:
                    min_diff = diff
                    best_rpm = rpm
        return best_rpm
    
    # Add Current Design point - determine which RPM curve it's on
    flow_current = flow_cclpa * (1 + CURRENT_DESIGN_MARGIN_PCT / 100)
    pressure_current = calculate_system_pressure(flow_current, flow_cclpa, pressure_cclpa)
    best_rpm_current = find_best_rpm_curve(flow_current, pressure_current, curve_data, rpm_values)
    eff_current = get_efficiency_at_flow(flow_current, best_rpm_current, curve_data)
    ax2.scatter(flow_current/1000, eff_current, s=200, c='red', 
               marker='X', edgecolors='black', linewidth=2, 
               zorder=5, label='Current Design\n(132% CCLPA)')
    
    # Add API 560 point (115% of CCLPA) - use consistent system curve
    flow_api = flow_cclpa * 1.15
    pressure_api = calculate_system_pressure(flow_api, flow_cclpa, pressure_cclpa)
    best_rpm_api = find_best_rpm_curve(flow_api, pressure_api, curve_data, rpm_values)
    eff_api = get_efficiency_at_flow(flow_api, best_rpm_api, curve_data)
    ax2.scatter(flow_api/1000, eff_api, s=200, c='purple', 
               marker='*', edgecolors='black', linewidth=2, 
               zorder=5, label='API 560\n(115% CCLPA)')
    
    ax2.axhline(y=80, color='red', linestyle='--', linewidth=2, 
               label='Requirement (80%)', alpha=0.7)
    
    ax2.set_xlabel('Flow Rate (×1000 m³/h)', fontsize=11, fontweight='bold')
    ax2.set_ylabel('Fan Efficiency (%)', fontsize=11, fontweight='bold')
    ax2.set_title('Fan Efficiency vs Flow', fontsize=12, fontweight='bold')
    ax2.legend(loc='lower right', fontsize=9)
    ax2.grid(True, alpha=0.3)
    ax2.set_ylim(0, 95)
    ax2.set_xlim(50, 220)
    
    # ========================================================================
    # PLOT 3: Motor Efficiency Curve
    # ========================================================================
    ax3 = fig.add_subplot(gs[1, 0])
    
    load_range = np.linspace(0, 120, 200)
    motor_eff = motor_efficiency_curve(load_range)
    # VFD efficiency at various speeds (approximate speed from load)
    speed_range = (load_range / 100) ** (1/3) * 100  # Approximate speed %
    vfd_eff = vfd_efficiency_curve(load_range, speed_range/100)
    combined_eff = motor_eff * vfd_eff
    
    ax3.plot(load_range, motor_eff * 100, linewidth=2, color='#d62728', 
            label='Motor Efficiency', linestyle='-')
    ax3.plot(load_range, vfd_eff * 100, linewidth=2, color='#9467bd', 
            label='VFD Efficiency', linestyle='--')
    ax3.plot(load_range, combined_eff * 100, linewidth=2, color='#2ca02c', 
            label='Combined (Motor × VFD)', linestyle='-.')
    
    ax3.axhline(y=96, color='green', linestyle=':', linewidth=1.5, 
               label='Peak Efficiency Zone', alpha=0.5)
    ax3.axvspan(60, 80, alpha=0.15, color='green', label='Optimal Load Range')
    
    # Mark current design operating point
    current_results = calculate_lifecycle_cost(CURRENT_DESIGN_MARGIN_PCT, use_part_load_profile=True)
    ax3.scatter(current_results['motor_load_pct'], 
               current_results['motor_efficiency'] * 100,
               s=200, c='red', marker='X', edgecolors='black', 
               linewidth=2, zorder=5, label='Current Design')
    
    # Mark API 560 operating point
    api_results = calculate_lifecycle_cost(15, use_part_load_profile=True)
    ax3.scatter(api_results['motor_load_pct'], 
               api_results['motor_efficiency'] * 100,
               s=200, c='purple', marker='*', edgecolors='black', 
               linewidth=2, zorder=5, label='API 560')
    
    ax3.set_xlabel('Motor Load (%)', fontsize=11, fontweight='bold')
    ax3.set_ylabel('Efficiency (%)', fontsize=11, fontweight='bold')
    ax3.set_title('Motor & VFD Efficiency vs Load', fontsize=12, fontweight='bold')
    ax3.legend(loc='lower right', fontsize=8, ncol=2)
    ax3.grid(True, alpha=0.3)
    ax3.set_xlim(0, 120)
    ax3.set_ylim(50, 100)
    
    # ========================================================================
    # PLOT 4: Sensitivity - Total Lifecycle Cost vs Design Margin
    # ========================================================================
    ax4 = fig.add_subplot(gs[1, 1])
    
    margins, results = sensitivity_analysis(use_part_load_profile=True)
    total_costs = [r['total_lifecycle_cost'] for r in results]
    
    ax4.plot(margins, np.array(total_costs)/1000, linewidth=3, 
            color='#e377c2', marker='o', markersize=4)
    
    # Mark key points
    api_idx = np.argmin(np.abs(margins - 15))
    current_idx = np.argmin(np.abs(margins - CURRENT_DESIGN_MARGIN_PCT))
    
    # Mark API 560 point (plotted but not in legend)
    ax4.scatter(15, total_costs[api_idx]/1000, s=300, c='purple', 
               marker='*', edgecolors='black', linewidth=2, 
               zorder=5)
    # Mark Current Design point (plotted but not in legend)
    ax4.scatter(CURRENT_DESIGN_MARGIN_PCT, total_costs[current_idx]/1000, s=250, c='red', 
               marker='X', edgecolors='black', linewidth=2, 
               zorder=5)
    
    # Best practice range
    ax4.axvspan(10, 15, alpha=0.2, color='green', 
               label='Industry Best Practice')
    
    ax4.set_xlabel('Design Margin over Nominal (%)', fontsize=11, fontweight='bold')
    ax4.set_ylabel('Total Lifecycle Cost (€ thousands)', fontsize=11, fontweight='bold')
    ax4.set_title('Lifecycle Cost Sensitivity to Design Margin', 
                 fontsize=12, fontweight='bold')
    ax4.legend(loc='upper left', fontsize=9)
    ax4.grid(True, alpha=0.3)
    
    # ========================================================================
    # PLOT 5: Sensitivity - Annual OPEX vs Design Margin
    # ========================================================================
    ax5 = fig.add_subplot(gs[1, 2])
    
    annual_opex = [r['annual_opex_eur'] for r in results]
    
    ax5.plot(margins, np.array(annual_opex)/1000, linewidth=3, 
            color='#bcbd22', marker='s', markersize=4)
    
    ax5.scatter(15, annual_opex[api_idx]/1000, s=300, c='purple',
               marker='*', edgecolors='black', linewidth=2, zorder=5)
    ax5.scatter(CURRENT_DESIGN_MARGIN_PCT, annual_opex[current_idx]/1000, s=250, c='red', 
               marker='X', edgecolors='black', linewidth=2, zorder=5)
    
    ax5.axvspan(10, 15, alpha=0.2, color='green')
    
    ax5.set_xlabel('Design Margin over Nominal (%)', fontsize=11, fontweight='bold')
    ax5.set_ylabel('Annual OPEX (€ thousands)', fontsize=11, fontweight='bold')
    ax5.set_title('Annual Operating Cost Sensitivity', fontsize=12, fontweight='bold')
    ax5.grid(True, alpha=0.3)
    
    # ========================================================================
    # PLOT 6: Sensitivity - Motor Load % vs Design Margin
    # ========================================================================
    ax6 = fig.add_subplot(gs[2, 0])
    
    motor_loads = [r['motor_load_pct'] for r in results]
    
    ax6.plot(margins, motor_loads, linewidth=3, color='#17becf', 
            marker='d', markersize=4)
    
    ax6.scatter(15, motor_loads[api_idx], s=300, c='purple',
               marker='*', edgecolors='black', linewidth=2, zorder=5)
    ax6.scatter(CURRENT_DESIGN_MARGIN_PCT, motor_loads[current_idx], s=250, c='red', 
               marker='X', edgecolors='black', linewidth=2, zorder=5)
    
    # Optimal load range
    ax6.axhspan(60, 80, alpha=0.2, color='green', 
               label='Optimal Load Range')
    ax6.axhline(y=50, color='orange', linestyle='--', linewidth=2, 
               label='Low Efficiency Threshold', alpha=0.7)
    
    ax6.set_xlabel('Design Margin over Nominal (%)', fontsize=11, fontweight='bold')
    ax6.set_ylabel('Motor Load at Nominal Operation (%)', fontsize=11, fontweight='bold')
    ax6.set_title('Motor Loading Sensitivity', fontsize=12, fontweight='bold')
    ax6.legend(loc='upper right', fontsize=9)
    ax6.grid(True, alpha=0.3)
    ax6.set_ylim(30, 90)
    
    # ========================================================================
    # PLOT 7: Sensitivity - CO2 Emissions vs Design Margin
    # ========================================================================
    ax7 = fig.add_subplot(gs[2, 1])
    
    co2_emissions = [r['annual_co2_tons'] for r in results]
    
    ax7.plot(margins, co2_emissions, linewidth=3, color='#8c564b', 
            marker='h', markersize=4)
    
    ax7.scatter(15, co2_emissions[api_idx], s=300, c='purple',
               marker='*', edgecolors='black', linewidth=2, zorder=5)
    ax7.scatter(CURRENT_DESIGN_MARGIN_PCT, co2_emissions[current_idx], s=250, c='red', 
               marker='X', edgecolors='black', linewidth=2, zorder=5)
    
    ax7.axvspan(10, 15, alpha=0.2, color='green')
    
    ax7.set_xlabel('Design Margin over Nominal (%)', fontsize=11, fontweight='bold')
    ax7.set_ylabel('Annual CO2 Emissions (tons)', fontsize=11, fontweight='bold')
    ax7.set_title('Environmental Impact Sensitivity', fontsize=12, fontweight='bold')
    ax7.grid(True, alpha=0.3)
    
    # ========================================================================
    # PLOT 8: Cost Breakdown Comparison
    # ========================================================================
    ax8 = fig.add_subplot(gs[2, 2])
    
    # Compare three scenarios: 10%, 15% (API), Current Design
    scenarios = [10, 15, int(round(CURRENT_DESIGN_MARGIN_PCT))]
    scenario_results = [calculate_lifecycle_cost(m, verbose=True, use_part_load_profile=True) for m in scenarios]
    
    labels = ['Best\nPractice\n(110% CCLPA)', 'API 560\n(115% CCLPA)', 'Current\n(132% CCLPA)']
    capex = [r['capex_eur']/1000 for r in scenario_results]
    opex = [r['npv_opex_eur']/1000 for r in scenario_results]
    
    x = np.arange(len(labels))
    width = 0.35
    
    bars1 = ax8.bar(x - width/2, capex, width, label='CAPEX', color='#ff9999')
    bars2 = ax8.bar(x + width/2, opex, width, label='NPV OPEX (30yr)', color='#66b3ff')
    
    # Add total cost labels
    for i, (c, o) in enumerate(zip(capex, opex)):
        total = c + o
        ax8.text(i, total + 5, f'€{total:.0f}k', ha='center', 
                fontweight='bold', fontsize=10)
    
    ax8.set_ylabel('Cost (€ thousands)', fontsize=11, fontweight='bold')
    ax8.set_title('Lifecycle Cost Breakdown by Design Approach', 
                 fontsize=12, fontweight='bold')
    ax8.set_xticks(x)
    ax8.set_xticklabels(labels)
    ax8.legend(loc='upper left', fontsize=9)
    ax8.grid(True, alpha=0.3, axis='y')
    
    # ========================================================================
    # Main title
    # ========================================================================
    fig.suptitle('Booster Fan Comprehensive Performance & Economic Analysis\nProject: Protos (P-3519)', 
                fontsize=16, fontweight='bold', y=0.995)
    
    plt.tight_layout(rect=[0, 0, 1, 0.99])
    
    return fig

# ============================================================================
# SUMMARY TABLE
# ============================================================================

def print_summary_table():
    """Print detailed comparison table and export to CSV"""
    print("\n" + "="*100)
    print("SENSITIVITY ANALYSIS SUMMARY - KEY DESIGN MARGINS")
    print("="*100)
    
    margins_to_analyze = [10, 12, 15, 18, 20, 22, 25, 30]
    
    print(f"\n{'Margin':<8} {'Motor':<10} {'Motor':<10} {'Motor':<10} {'Annual':<12} {'CAPEX':<12} "
          f"{'NPV OPEX':<12} {'Total LC':<12} {'CO2/yr':<10}")
    print(f"{'(%)':<8} {'Size(kW)':<10} {'Load(%)':<10} {'Eff(%)':<10} {'OPEX(€)':<12} {'(€)':<12} "
          f"{'30yr(€)':<12} {'Cost(€)':<12} {'(tons)':<10}")
    print("-"*100)
    
    baseline = calculate_lifecycle_cost(15, use_part_load_profile=True)  # API 560 as baseline
    
    # Prepare data for CSV export
    csv_data = []
    
    for margin in margins_to_analyze:
        result = calculate_lifecycle_cost(margin, use_part_load_profile=True)
        
        # Calculate deltas vs API 560
        delta_capex = result['capex_eur'] - baseline['capex_eur']
        delta_opex = result['annual_opex_eur'] - baseline['annual_opex_eur']
        delta_total = result['total_lifecycle_cost'] - baseline['total_lifecycle_cost']
        delta_co2 = result['annual_co2_tons'] - baseline['annual_co2_tons']
        
        marker = ""
        if margin == 15:
            marker = "API 560"
        elif margin == 25:
            marker = "CURRENT"
        elif margin <= 12:
            marker = "Best Practice"
        else:
            marker = ""
        
        # Store data for CSV
        csv_data.append({
            'Design_Margin_%': margin,
            'Design_Type': marker,
            'Motor_Size_kW': round(result['motor_rated_kw'], 1),
            'Motor_Load_%': round(result['motor_load_pct'], 1),
            'Motor_Efficiency_%': round(result['motor_efficiency'] * 100, 1),
            'Annual_OPEX_EUR': round(result['annual_opex_eur'], 0),
            'CAPEX_EUR': round(result['capex_eur'], 0),
            'NPV_OPEX_30yr_EUR': round(result['npv_opex_eur'], 0),
            'Total_Lifecycle_Cost_EUR': round(result['total_lifecycle_cost'], 0),
            'Annual_CO2_Tons': round(result['annual_co2_tons'], 1),
            'Delta_OPEX_vs_API_EUR': round(delta_opex, 0),
            'Delta_CAPEX_vs_API_EUR': round(delta_capex, 0),
            'Delta_NPV_OPEX_vs_API_EUR': round(delta_total - delta_capex, 0),
            'Delta_Total_LC_vs_API_EUR': round(delta_total, 0),
            'Delta_CO2_vs_API_Tons': round(delta_co2, 1),
            'Flow_Design_m3h': round(result['flow_design'], 0),
            'Pressure_Design_mbar': round(result['pressure_design'], 1),
            'Annual_Energy_kWh': round(result['annual_energy_kwh'], 0),
            'Annual_Maintenance_EUR': round(result.get('annual_maintenance_eur', 0), 0),
        })
        
        print(f"{margin:<8} {result['motor_rated_kw']:<10.1f} {result['motor_load_pct']:<10.1f} "
              f"{result['motor_efficiency']*100:<10.1f} {result['annual_opex_eur']:<12,.0f} "
              f"{result['capex_eur']:<12,.0f} {result['npv_opex_eur']:<12,.0f} "
              f"{result['total_lifecycle_cost']:<12,.0f} {result['annual_co2_tons']:<10.1f}{' [' + marker + ']' if marker else ''}")
        
        if margin != margins_to_analyze[0]:
            print(f"{'Delta vs API':<12} {'':<10} {'':<10} {'':<10} "
                  f"{delta_opex:+12,.0f} {delta_capex:+12,.0f} "
                  f"{delta_total - delta_capex:+12,.0f} {delta_total:+12,.0f} {delta_co2:+10.1f}")
    
    print("-"*100)
    key_insight = calculate_lifecycle_cost(16, use_part_load_profile=True)['total_lifecycle_cost'] - baseline['total_lifecycle_cost']
    print(f"\nKey Insight: Every 1% increase in design margin above API 560 costs approximately €{key_insight:.0f} in lifecycle costs")
    print("="*100)
    
    # Export to CSV
    script_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
    csv_file = os.path.join(script_dir, 'Sensitivity_Analysis_Summary.csv')
    
    # Define CSV column headers
    fieldnames = [
        'Design_Margin_%',
        'Design_Type',
        'Motor_Size_kW',
        'Motor_Load_%',
        'Motor_Efficiency_%',
        'Annual_OPEX_EUR',
        'CAPEX_EUR',
        'NPV_OPEX_30yr_EUR',
        'Total_Lifecycle_Cost_EUR',
        'Annual_CO2_Tons',
        'Delta_OPEX_vs_API_EUR',
        'Delta_CAPEX_vs_API_EUR',
        'Delta_NPV_OPEX_vs_API_EUR',
        'Delta_Total_LC_vs_API_EUR',
        'Delta_CO2_vs_API_Tons',
        'Flow_Design_m3h',
        'Pressure_Design_mbar',
        'Annual_Energy_kWh',
        'Annual_Maintenance_EUR',
    ]
    
    try:
        with open(csv_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_data)
        
        print(f"\n[OK] Sensitivity analysis summary exported to CSV: {csv_file}")
    except Exception as e:
        print(f"\n[WARNING] Failed to export CSV: {e}")

# ============================================================================
# MAIN EXECUTION
# ============================================================================

if __name__ == "__main__":
    print("="*80)
    print("BOOSTER FAN ENHANCED VISUALIZATION & SENSITIVITY ANALYSIS")
    print("="*80)
    
    # Generate comprehensive plots
    fig = create_comprehensive_plots()
    
    # Save figure
    # Save figure in the same directory as the script
    import os
    script_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
    output_file = os.path.join(script_dir, 'Booster_Fan_Comprehensive_Analysis.png')
    fig.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"\n[OK] Comprehensive analysis plot saved: {output_file}")
    
    # Print summary table
    print_summary_table()
    
    # Calculate and display key recommendations
    print("\n" + "="*80)
    print("RECOMMENDATION SUMMARY")
    print("="*80)
    
    current = calculate_lifecycle_cost(CURRENT_DESIGN_MARGIN_PCT, use_part_load_profile=True)
    api = calculate_lifecycle_cost(15, use_part_load_profile=True)
    optimal = calculate_lifecycle_cost(12, use_part_load_profile=True)
    
    print(f"\nCurrent Design (132% CCLPA):")
    print(f"  - Total Lifecycle Cost: €{current['total_lifecycle_cost']:,.0f}")
    print(f"  - Motor operates at {current['motor_load_pct']:.1f}% load (inefficient)")
    print(f"  - Motor efficiency: {current['motor_efficiency']*100:.1f}%")
    
    print(f"\nAPI 560 Design (115% CCLPA):")
    print(f"  - Total Lifecycle Cost: €{api['total_lifecycle_cost']:,.0f}")
    print(f"  - Savings vs Current: €{current['total_lifecycle_cost'] - api['total_lifecycle_cost']:,.0f}")
    print(f"  - Motor operates at {api['motor_load_pct']:.1f}% load (near optimal)")
    print(f"  - Motor efficiency: {api['motor_efficiency']*100:.1f}%")
    
    print(f"\nBest Practice Design (110% CCLPA):")
    print(f"  - Total Lifecycle Cost: €{optimal['total_lifecycle_cost']:,.0f}")
    print(f"  - Savings vs Current: €{current['total_lifecycle_cost'] - optimal['total_lifecycle_cost']:,.0f}")
    print(f"  - Motor operates at {optimal['motor_load_pct']:.1f}% load (optimal)")
    print(f"  - Motor efficiency: {optimal['motor_efficiency']*100:.1f}%")
    
    print("\n" + "="*80)
    print("Analysis complete! Check the generated visualization.")
    print("="*80)
