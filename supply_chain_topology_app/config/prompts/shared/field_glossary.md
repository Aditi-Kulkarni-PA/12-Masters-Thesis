### Field Glossary 

**Prediction output fields**
- **predict_delay**: Stage-1 classifier flag -- 1 = predicted delayed, 0 = on time
- **predict_severity_label**: Stage-2 severity -- Short (1-2h), Medium (3-5h), Long (6+h)
- **delay_reason**: Rule-based hint pre-computed by the pipeline (not LLM-written; never modify it)
- **llm_insights**: Agent-written cross-feature explanation per delayed row

**Derived features (engineered by the ML pipeline)**
_Fields marked [row] are included in each delayed-order row sent to the predict agent (selected by Random Forest feature importance, shown in parentheses); the rest appear in DB summary tables used by diagnosis/recommendation._
- **km_per_expected_hr** [row] (27.1% -- strongest predictor): distance_km / (expected_time_hrs + small epsilon) -- schedule tightness; higher = more aggressive delivery window
- **mode_urgency** [row] (21.5%): Ordinal delivery-mode urgency -- Standard=1, Two Day=2, Express=3, Same Day=4
- **schedule_risk** [row] (14.9%): weather_severity x mode_urgency (0-16) -- compounding weather-urgency pressure; 0 = no risk, 16 = maximum
- **vehicle_load_strain** [row] (~10%): (package_weight_kg x distance_km) / vehicle_capacity -- how overloaded the vehicle is for the route
- **carrier_avg_schedule** [row] (~8%): Mean km_per_expected_hr per delivery_partner -- identifies partners who systematically accept routes too tight for their fleet
- **weather_severity** [row] (~7%): Ordinal weather encoding -- Clear=0, Hot/Cold=1, Foggy=2, Rainy=3, Stormy=4
- **weight_x_distance** [row] (~5%): package_weight_kg x distance_km -- load-distance burden interaction
- **cost_per_kg** [row] (~3%): delivery_cost / (package_weight_kg + epsilon) -- weight-adjusted pricing; under-priced heavy packages may be deprioritised by partners
- **vehicle_type** [row]: Vehicle assigned -- Bike / EV / Van / Truck
- **vehicle_capacity**: Ordinal carrying capacity -- Bike=1, EV=2, Van=3, Truck=4
- **carrier_avg_weight**: Mean package_weight_kg per delivery_partner
- **distance_category**: short (< 50 km), medium (50-200 km), long (> 200 km)

**Diagnosis / summary-table fields (per group)**
- **total_deliveries**: Count of deliveries in this group
- **delayed_count**: Number of delayed deliveries in this group
- **on_time_count**: Number of on-time deliveries in this group
- **avg_distance_km**: Average delivery distance in km for this group
- **avg_package_weight_kg**: Average package weight in kg for this group
- **delay_rate**: Fraction delayed (delayed_count / total_deliveries)
- **severity_short/medium/long_count**: Delay severity buckets -- Short (1-2h), Medium (3-5h), Long (6+h)
- **avg_schedule_risk**: Average schedule_risk (weather_severity x mode_urgency) across the group
- **pattern_type**: High-risk combination type (e.g. mode_weather, weather_vehicle, mode_distance)
- **pattern_description**: Human-readable combination (e.g. "same_day + Stormy")
- **risk_level**: medium (30-40% delay rate), high (40-50%), critical (50%+)
- **rate_change_pct**: daily delay rate minus historical -- negative means improvement
