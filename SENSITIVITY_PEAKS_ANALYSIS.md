# Analysis: Unexpected Peaks in Sensitivity Analysis Curves

**Issue Reported**: Sensitivity analysis plots show inappropriate peaks around 12% and 25% design margin instead of smooth curves.

**Date**: 2025-11-09
**Severity**: HIGH - Affects validity of sensitivity analysis results

---

## ROOT CAUSE ANALYSIS

### The Problem

The `get_fan_efficiency_at_operating_point()` function (lines 152-207) uses **discrete RPM curve selection**:

```python
# Current logic:
rpm_values = [1200, 1350, 1500]  # Only 3 discrete curves

# For each operating point:
1. Generate fan curves at 1200, 1350, 1500 RPM
2. Find which curve gives pressure closest to target
3. Pick efficiency from that ONE curve
```

### Why This Causes Peaks

**Scenario as design margin increases:**

| Design Margin | Design Flow | Operating Point (CCLPA) Speed | Best Matching RPM Curve | Effect |
|---------------|-------------|-------------------------------|------------------------|---------|
| 10% | 148 km³/h | ~91% of design | **1500 RPM** | High efficiency |
| 12% | 151 km³/h | ~89% of design | **Switches to 1350 RPM** | **DISCONTINUITY** |
| 15% | 155 km³/h | ~87% of design | **1350 RPM** | Medium efficiency |
| 25% | 168 km³/h | ~80% of design | **Switches to 1200 RPM** | **DISCONTINUITY** |
| 30% | 175 km³/h | ~77% of design | **1200 RPM** | Lower efficiency |

**At the switch points** (around 12% and 25%), the function **abruptly switches** which RPM curve it uses, causing:
- Discontinuous jump in efficiency
- Corresponding peak or valley in lifecycle cost
- Non-smooth sensitivity curves

### Mathematical Explanation

The current function picks efficiency like this:

```
eff(flow, pressure) = eff_curve[best_rpm](flow)

where best_rpm = argmin_{rpm ∈ {1200,1350,1500}} |P_curve[rpm](flow) - pressure_target|
```

This creates a **piecewise function with discontinuities** at the boundaries where `best_rpm` switches.

**Reality**: With VFD control, the fan runs at a **continuous RPM**, not discrete values. Efficiency should be a smooth function of operating conditions.

---

## ILLUSTRATION OF THE ISSUE

```
Lifecycle Cost vs Design Margin (Current - Incorrect):

Cost
 │      ╱╲
 │     ╱  ╲        ╱╲
 │    ╱    ╲      ╱  ╲
 │   ╱      ╲    ╱    ╲___
 │  ╱        ╲__╱
 │ ╱
 └────────────────────────────► Design Margin %
    10  12  15   20  25  30
        ↑         ↑
        Peak1     Peak2
   (RPM switch) (RPM switch)
```

**Expected (Correct):**

```
Cost
 │
 │           ╱────
 │         ╱
 │       ╱
 │     ╱
 │   ╱
 │  ╱
 └────────────────────────────► Design Margin %
    10  12  15   20  25  30
       (Smooth curve)
```

---

## WHY CURRENT APPROACH IS WRONG

### Problem 1: Discrete RPM Curves
- Only 3 RPM values considered
- Creates artificial boundaries
- Real fans with VFD operate at ANY RPM continuously

### Problem 2: "Winner Takes All" Selection
- Picks ONE curve entirely
- Ignores that operating point might be BETWEEN two curves
- No interpolation across RPM dimension

### Problem 3: Operating Point Inconsistency
As design margin changes:
- Design point moves along system curve (higher flow, higher pressure)
- Operating points (CCLPA, CCLPB, CCLPE) stay constant (system requirement)
- Fan speed at operating points decreases (VFD reduces speed)
- At certain margins, the operating point "crosses over" to being better matched by a different discrete RPM curve

---

## EVIDENCE IN THE CODE

From `get_fan_efficiency_at_operating_point()`:

```python
# Lines 176-191: Find best matching RPM
for rpm in rpm_values:  # Only [1200, 1350, 1500]
    Q, P, eff = curves[rpm]

    # Check if flow is within curve range
    if flow_m3h < Q[0] or flow_m3h > Q[-1]:
        continue

    # Interpolate pressure at this flow on this RPM curve
    pressure_at_flow = np.interp(flow_m3h, Q, P)

    # Check how close the pressure matches
    pressure_diff = abs(pressure_at_flow - pressure_mbar)

    if pressure_diff < min_pressure_diff:
        min_pressure_diff = pressure_diff
        best_rpm = rpm  # ← DISCONTINUOUS SELECTION
```

**Issue**: `best_rpm` can only be 1200, 1350, or 1500. When it switches from one to another, efficiency jumps discontinuously.

---

## IMPACT ON RESULTS

### Affected Plots:
1. **Plot 4**: Lifecycle Cost vs Design Margin - Shows peaks
2. **Plot 5**: Annual OPEX vs Design Margin - Shows peaks
3. **Plot 6**: Motor Load vs Design Margin - May show steps
4. **Plot 7**: CO2 Emissions vs Design Margin - Shows peaks

### Data Validity:
- **Trends are correct** (higher margins → higher costs)
- **Absolute values at peaks are WRONG** (discontinuities are artificial)
- **Optimal design margin identification is UNRELIABLE** (may pick a peak as minimum)

---

## PROPOSED SOLUTIONS

### Solution 1: Bilinear Interpolation (RECOMMENDED)
Interpolate efficiency across BOTH flow AND RPM dimensions:

```python
def get_fan_efficiency_interpolated(flow, pressure):
    # 1. Determine required RPM from affinity laws
    #    At operating point: pressure = f(flow, rpm)
    #    Solve for rpm given flow and pressure

    # 2. Generate curves at two bounding RPMs
    #    e.g., if rpm_required = 1420, use curves at 1350 and 1500

    # 3. Interpolate efficiency at target flow on both curves
    #    eff_1350 = interp(flow, Q_1350, eff_1350)
    #    eff_1500 = interp(flow, Q_1500, eff_1500)

    # 4. Interpolate between the two RPM curves
    #    eff_final = interp(rpm_required, [1350, 1500], [eff_1350, eff_1500])

    return eff_final
```

**Advantage**: Smooth interpolation, no discontinuities

### Solution 2: Finer RPM Grid
Use more RPM values: [1100, 1150, 1200, 1250, 1300, 1350, 1400, 1450, 1500]

**Advantage**: Simple to implement
**Disadvantage**: Still has discontinuities, just smaller

### Solution 3: Analytical Efficiency Model
Create continuous efficiency model: `eff = f(flow, rpm)` based on fan type

**Advantage**: Physically accurate
**Disadvantage**: Requires more fan-specific data

---

## RECOMMENDED FIX: Solution 1 (Bilinear Interpolation)

### Implementation Steps:

1. **Calculate actual RPM** required at operating point from affinity laws
2. **Find bounding RPMs** in the available curves
3. **Interpolate efficiency** at target flow on both bounding curves
4. **Interpolate between RPMs** to get final efficiency

### Code Structure:

```python
def get_fan_efficiency_at_operating_point_smooth(flow_m3h, pressure_mbar):
    """
    Smoothly interpolate fan efficiency using bilinear interpolation
    across flow and RPM dimensions
    """
    # Step 1: Estimate required RPM from affinity laws and system curve
    rpm_required = estimate_rpm_for_operating_point(flow_m3h, pressure_mbar)

    # Step 2: Find bounding RPMs
    rpm_values_full = np.arange(1100, 1550, 50)  # Finer grid
    rpm_lower = rpm_values_full[rpm_values_full <= rpm_required][-1]
    rpm_upper = rpm_values_full[rpm_values_full >= rpm_required][0]

    # Step 3: Get efficiency at both bounding RPMs
    Q_lower, P_lower, eff_lower = generate_fan_curve(rpm_lower)
    Q_upper, P_upper, eff_upper = generate_fan_curve(rpm_upper)

    eff_at_lower_rpm = np.interp(flow_m3h, Q_lower, eff_lower)
    eff_at_upper_rpm = np.interp(flow_m3h, Q_upper, eff_upper)

    # Step 4: Interpolate between RPMs
    if rpm_lower == rpm_upper:
        eff_final = eff_at_lower_rpm
    else:
        eff_final = np.interp(rpm_required,
                             [rpm_lower, rpm_upper],
                             [eff_at_lower_rpm, eff_at_upper_rpm])

    return eff_final
```

---

## VALIDATION APPROACH

After implementing fix:

1. **Plot efficiency vs design margin** - should be smooth
2. **Plot lifecycle cost vs design margin** - should be monotonic or smooth
3. **Check for discontinuities** - should be none
4. **Verify optimal margin** - should be at a smooth minimum, not a peak

---

## CONCLUSION

The peaks at 12% and 25% design margin are **artifacts** caused by discrete RPM curve selection. They do NOT represent real physical phenomena.

**Action Required**: Implement bilinear interpolation to create smooth, physically realistic sensitivity curves.

**Priority**: HIGH - Current results may lead to incorrect design decisions.

---

**End of Analysis**
