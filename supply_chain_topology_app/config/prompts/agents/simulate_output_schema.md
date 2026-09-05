**Simulation result — required output structure**

- `simulations` (list) — *List of simulations for order delivery delays*
  - `delivery_id` (str) — *Delivery ID*
  - `delivery_partner` (str) — *Delivery Partner*
  - `delivery_mode` (str) — *Delivery Mode*
  - `region` (str) — *Region*
  - `weather_condition` (str) — *Simulated weather condition*
  - `vehicle_type` (str) — *Simulated vehicle type*
  - `distance_km` (str) — *Distance in km*
  - `original_severity` (str) — *Original predicted severity label*
  - `simulated_severity` (str) — *Simulated severity under new conditions*
  - `simulate_delay_reason` (str, optional) — *Reason for simulated delay*
