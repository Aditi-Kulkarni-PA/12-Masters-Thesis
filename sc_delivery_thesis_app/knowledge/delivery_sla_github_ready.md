# Service Level Agreement (SLA)

## Multi-Partner Delivery Operations — India Network

| Field | Value |
| --- | --- |
| Effective Date | February 1, 2026 |
| Version | 3.0 |
| Document Owner | Supply Chain Operations |
| Review Cycle | Monthly |
| Geographic Scope | Pan-India — 5 regions (North, South, East, West, Central) |
| Daily Order Volume | ~5,000 orders/day |
| Historical Baseline | 20,000 orders across all partners |

---

## Executive Summary

This Service Level Agreement (SLA) defines delivery performance standards, partner commitments, customer experience guarantees, and escalation procedures for the multi-partner logistics network serving e-commerce order fulfillment across India. This agreement covers all **nine active delivery partners** — Amazon Logistics, Blue Dart, Delhivery, DHL, Ecom Express, Ekart, FedEx, Shadowfax, and XpressBees — and establishes unified performance benchmarks, penalties, and improvement mechanisms across four delivery modes (Standard, Two Day, Same Day, Express), six weather conditions, six vehicle types, and nine package categories.

---

## 1. Service Overview

### 1.1 Scope of Services

This SLA covers end-to-end delivery operations for the following service types:

- **Standard delivery** (3–5 business days) — lowest delay risk; historically 0% delay rate
- **Two-day delivery** (2 business days) — near-zero delay risk; historically <1% delay rate
- **Same-day delivery** (metro and major cities) — moderate delay risk; historically ~32% delay rate
- **Express delivery** (1–2 business days) — highest delay risk; historically ~74% delay rate
- Cash-on-delivery (COD) and prepaid orders
- Return pickups and reverse logistics
- Real-time tracking and customer notifications
- Multi-partner route optimization and allocation

### 1.2 Geographic Coverage — Regions

Operations span five geographic regions with dedicated operational zones:

| Region | Key Cities | Avg Distance (km) | Historical Delay Rate |
| --- | --- | --- | --- |
| North | Delhi, NCR, Punjab, Haryana, Lucknow | 149.3 | 26.5% |
| South | Bengaluru, Hyderabad, Chennai, Kochi | 148.7 | 26.8% |
| East | Kolkata, Bhubaneswar, Patna, Guwahati | 150.2 | 25.9% |
| West | Mumbai, Pune, Ahmedabad, Jaipur | 151.9 | 27.2% |
| Central | Indore, Bhopal, Nagpur, Raipur | 152.0 | 26.9% |

Table 1: Regional coverage and historical delay rates

### 1.3 Service Exclusions

The following are explicitly excluded from this SLA:

- Orders with incorrect or incomplete delivery addresses
- Delays caused by natural disasters, riots, or government-imposed lockdowns
- Customer unavailability for three consecutive delivery attempts
- Restricted or prohibited items as per partner policies
- International shipments (separate SLA applicable)
- Orders placed during major sale events (surge SLA applicable)

---

## 2. Delivery Performance Targets

### 2.1 On-Time Delivery Commitments by Mode

Performance targets are set per delivery mode. Historical baseline data (20,000 orders) informs achievable vs aspirational targets:

| Delivery Mode | Historical OTD | Current Target OTD | Aspirational Target | Typical Volume Share |
| --- | --- | --- | --- | --- |
| Standard | 100.0% | 99% | 99.5% | ~25% |
| Two Day | 99.6% | 98% | 99% | ~25% |
| Same Day | 67.5% | 75% | 85% | ~25% |
| Express | 26.1% | 40% | 60% | ~25% |

Table 2: On-time delivery targets by delivery mode

> **Critical Note:** Express delivery currently operates far below acceptable SLA levels with only 26.1% on-time delivery historically. Improving express delivery performance is the #1 strategic priority. Any recommendation system must flag express mode delays as highest priority.

### 2.2 Measurement Methodology

On-time delivery percentage is calculated as follows:

- "On time" means delivered within the promised delivery window, before 8 PM local time.
- Measurements are aggregated daily and reported weekly.
- Failed delivery attempts (customer unavailable) are excluded if three attempts were made.
- RTO (Return to Origin) is counted as a delivery failure.
- Partner-wise, route-wise, and mode-wise OTD is tracked separately for optimization.

### 2.3 Delay Severity Classification

When a delivery is delayed, severity is classified into three tiers based on the extent of the delay:

| Severity Level | Delay Duration | Historical Share of Delays | Typical Cause Profile |
| --- | --- | --- | --- |
| Short | 1–2 hours | ~50.6% (2,700 of 5,335) | Minor traffic, last-mile hiccups, address lookup |
| Medium | 3–5 hours | ~39.7% (2,119 of 5,335) | Weather impact, hub congestion, partner capacity |
| Long | 6+ hours | ~11.9% (635 of 5,335) | Severe weather, route failure, vehicle breakdown, partner outage |

Table 3: Delay severity tiers and historical distribution

> **Operational Rule:** Long-severity delays (6+ hours) require immediate escalation to Level 3 (Operations Manager). Medium-severity delays trigger automated rerouting evaluation. Short-severity delays are handled at partner level with customer notification.

### 2.4 First Attempt Delivery Rate

| Order Type | Target FADR |
| --- | --- |
| Prepaid Orders | 85% |
| Cash-on-Delivery (COD) | 75% |
| High-Value Orders (>₹10,000) | 90% |

Table 4: First Attempt Delivery Rate (FADR) targets

### 2.5 Transit Time Performance

Average time from order pickup to final delivery:

| Route Type | Target Transit | Metro to Metro | Metro to Tier 2/3 |
| --- | --- | --- | --- |
| Same-City | 8–12 hours | 10 hours | N/A |
| Within-Zone | 1–2 days | 1.5 days | 2.5 days |
| Cross-Zone | 3–4 days | 3 days | 4–5 days |
| Remote/Rural | 5–7 days | N/A | 6–8 days |

Table 5: Transit time benchmarks by route category

---

## 3. Weather Impact Policy and Mitigation

### 3.1 Weather-Specific Delay Risk Profiles

Weather is one of the strongest predictors of delivery delays. The following profiles are based on historical analysis of 20,000 deliveries:

| Weather Condition | Historical Delay Rate | Severity Distribution (S / M / L) | Risk Level | Schedule Risk Score |
| --- | --- | --- | --- | --- |
| Clear | 17.0% | 90% / 9% / 1% | Low | 0.0 |
| Cold | 16.2% | 79% / 13% / <1% | Low | 2.5 |
| Hot | 17.5% | 87% / 11% / 1% | Low | 2.5 |
| Foggy | 30.5% | 75% / 32% / 4% | Medium | 5.0 |
| Rainy | 36.9% | 32% / 59% / 15% | High | 7.4 |
| Stormy | 41.6% | 8% / 63% / 29% | Critical | 10.0 |

Table 6: Weather-specific delay risk profiles

### 3.2 Weather-Specific Operational Protocols

| Weather | Mandatory Actions |
| --- | --- |
| Clear / Cold / Hot | Standard operations; no special measures required. Monitor for heat-sensitive packages (cosmetics, pharmacy, groceries) in hot weather. |
| Foggy | Increase buffer time by 1–2 hours for express/same-day orders. Avoid bike/scooter assignments for long-distance. Activate fog-delay notifications to customers. Delay rate jumps to ~30%, primarily short-severity. |
| Rainy | Switch to enclosed vehicles (van, ev van, truck) for fragile items, electronics, and documents. Add 2–3 hour buffer for express deliveries. ~59% of rainy delays are medium-severity (3–5h). Proactively notify customers of potential delays. |
| Stormy | **Emergency protocol.** Halt same-day and express dispatches if wind speed > 60 km/h. Reroute to next-day delivery with customer consent. 29% of stormy delays are long-severity (6+ hours). Assign only truck/van vehicles. Storm surcharge waived for customers. |

Table 7: Weather-specific operational protocols

### 3.3 Weather + Delivery Mode Risk Matrix

Certain weather-mode combinations are extremely high-risk and require special handling:

| Combination | Historical Delay Rate | Risk Level | Required Action |
| --- | --- | --- | --- |
| Express + Stormy | 99.8% | Critical | Do not dispatch express in storms; auto-downgrade to two-day with customer notification |
| Express + Rainy | 97.5% | Critical | Add 4+ hour buffer; assign enclosed vehicle; pre-notify customer of likely delay |
| Express + Foggy | 89.4% | Critical | Add 2–3 hour buffer; avoid open vehicles; consider rescheduling if fog advisory active |
| Express + Cold | 53.0% | High | Monitor closely; no special vehicle restriction needed |
| Express + Hot | 52.3% | High | Monitor closely; ensure cold-chain for pharmacy/groceries |
| Express + Clear | 51.5% | High | Standard express protocol; inherently high base risk |
| Same Day + Stormy | 65.3% | Critical | Suspend same-day if storm warning active; offer next-day with voucher |
| Same Day + Rainy | 52.1% | Critical | Assign enclosed vehicle; extend delivery window by 3 hours |
| Same Day + Foggy | 34.8% | Medium | Add 1–2 hour buffer; standard vehicles acceptable |

Table 8: High-risk weather + delivery mode combinations

> **Recommendation Engine Rule:** When generating recommendations, always check the weather + mode matrix. If current weather is stormy or rainy and mode is express, the top recommendation must be to switch delivery mode or add significant buffer time.

---

## 4. Distance-Based Delivery Guidelines

### 4.1 Distance Categories and Risk

| Distance Category | Range | Historical Volume | Delay Rate | Avg Severity (S / M / L) |
| --- | --- | --- | --- | --- |
| Short | < 50 km | 3,295 (16.5%) | 17.1% | 32% / 35% / 4% |
| Medium | 50–200 km | 10,061 (50.3%) | 23.7% | 57% / 38% / 7% |
| Long | > 200 km | 6,644 (33.2%) | 35.9% | 49% / 42% / 18% |

Table 9: Distance-based delay risk profiles

### 4.2 Distance-Specific Operational Rules

- **Short distance (< 50 km):** Preferred for same-day and express delivery. Bike and scooter vehicles are acceptable. Delay risk is manageable (~17%) even in moderate weather.
- **Medium distance (50–200 km):** Core delivery segment handling 50% of all orders. Express mode at medium distance has ~74% delay rate — consider two-day mode for non-urgent orders. Use van or truck for packages > 20 kg.
- **Long distance (> 200 km):** Highest delay risk at 36%. Express + long distance combination has 78% delay rate. Strongly recommend standard or two-day mode for long distances. Always assign truck or van. Buffer an extra day during adverse weather.

### 4.3 Distance + Mode Risk Matrix

| Mode + Distance | Delay Rate | Recommendation |
| --- | --- | --- |
| Express + Long (>200 km) | 78.0% | Avoid; recommend two-day mode |
| Express + Medium (50–200 km) | 74.2% | High risk; add 4h buffer minimum |
| Express + Short (<50 km) | 65.0% | Acceptable with weather check |
| Same Day + Long (>200 km) | 63.4% | Avoid; offer next-day delivery |
| Same Day + Medium (50–200 km) | 21.6% | Acceptable; monitor weather |
| Same Day + Short (<50 km) | 2.3% | Low risk; standard operations |

Table 10: Distance + delivery mode combined delay risk

---

## 5. Vehicle Type Guidelines

### 5.1 Vehicle Fleet Performance

| Vehicle Type | Historical Delay Rate | Best For | Avoid When |
| --- | --- | --- | --- |
| Bike | 26.7% | Short distance, clear/cold/hot weather, lightweight packages (<10 kg) | Rainy/stormy weather, heavy packages, long distance |
| Scooter | 25.5% | Short-medium distance, lightweight packages | Stormy weather, packages > 15 kg |
| EV Bike | 26.9% | Urban same-day, short distance, eco-friendly routes | Long distance (range limitations), extreme weather |
| EV Van | 26.5% | Medium distance, enclosed protection, all package types | Very long distance (charging constraints) |
| Van | 26.9% | All distances, all weather (enclosed), fragile/heavy items | Not cost-effective for lightweight short-distance |
| Truck | 27.5% | Long distance, heavy/bulk packages, stormy weather (most stable) | Same-day urban delivery (maneuverability constraints) |

Table 11: Vehicle type performance and suitability guidelines

### 5.2 Weather-Vehicle Assignment Rules

| Weather | Recommended Vehicles | Avoid |
| --- | --- | --- |
| Clear / Cold / Hot | Any vehicle — assign by distance and package weight | None |
| Foggy | Van, EV Van, Truck (enclosed, better visibility equipment) | Bike, Scooter (open vehicles, visibility risk) |
| Rainy | Van, EV Van, Truck (enclosed, waterproof cargo area) | Bike, Scooter (water damage risk, road safety) |
| Stormy | Truck, Van only (most stable, enclosed) | All two-wheelers (safety hazard) |

Table 12: Weather-based vehicle assignment policy

---

## 6. Package Type Handling Requirements

### 6.1 Package Categories and Special Requirements

The network handles nine package categories, each with specific handling needs:

| Package Type | Historical Delay Rate | Avg Weight (kg) | Special Handling Requirements |
| --- | --- | --- | --- |
| Electronics | 27.5% | 25.3 | Shock-proof packaging; enclosed vehicle mandatory; avoid extreme temperatures |
| Fragile Items | 26.9% | 25.4 | Double-box packaging; "FRAGILE" marking; enclosed vehicle required; careful loading |
| Pharmacy | 27.4% | 25.1 | Temperature-controlled transport in hot weather; timely delivery critical; regulatory compliance |
| Groceries | 27.4% | 25.2 | Same-day preferred for perishables; cold-chain for dairy/frozen; immediate delivery priority |
| Cosmetics | 26.1% | 25.0 | Avoid extreme heat; enclosed vehicle in rain; medium fragility handling |
| Furniture | 25.1% | 25.1 | Truck/van only; two-person delivery team; appointment-based delivery preferred |
| Automobile Parts | 26.5% | 24.9 | Heavy-duty packaging; truck preferred for heavy items; oil-leak protection |
| Documents | 26.8% | 24.9 | Waterproof packaging in rain/storm; priority handling for legal/time-sensitive docs |
| Clothing | 26.4% | 25.3 | Standard handling; waterproof outer packaging in rain; low-risk category |

Table 13: Package type handling requirements and performance

### 6.2 Package + Weather Risk Rules

- **Electronics + Rainy/Stormy:** Mandatory enclosed vehicle. If express mode, add 2h buffer.
- **Pharmacy + Hot weather:** Cold-chain vehicle preferred. Monitor delivery time — delays beyond 4h may compromise product.
- **Groceries + Any delay risk > 30%:** Prioritize immediate dispatch or reschedule to avoid spoilage.
- **Fragile Items + Stormy:** Use truck only. Secure with additional padding. Notify customer of potential delay.
- **Documents + Rainy:** Waterproof packaging verification before dispatch. Enclosed vehicle mandatory.

---

## 7. Partner Performance Standards

### 7.1 Active Partner Network and Historical Performance

| Partner | Historical Volume | Delay Rate | Severity (S / M / L) | Performance Tier |
| --- | --- | --- | --- | --- |
| FedEx | 2,249 | 24.6% | 269 / 229 / 65 | Tier 1 (Best) |
| Delhivery | 2,244 | 24.9% | 275 / 220 / 64 | Tier 1 |
| DHL | 2,205 | 26.0% | 300 / 227 / 69 | Tier 2 |
| Ecom Express | 2,187 | 26.4% | 297 / 254 / 48 | Tier 2 |
| Blue Dart | 2,233 | 27.3% | 303 / 207 / 103 | Tier 2 |
| Shadowfax | 2,207 | 27.3% | 321 / 235 / 70 | Tier 2 |
| Amazon Logistics | 2,166 | 28.1% | 307 / 241 / 75 | Tier 3 |
| Ekart | 2,256 | 27.8% | 316 / 244 / 67 | Tier 3 |
| XpressBees | 2,253 | 27.7% | 312 / 262 / 74 | Tier 3 |

Table 14: Partner performance tiers based on historical data

### 7.2 Partner Performance Tiers and Routing Priority

- **Tier 1 (Delay Rate < 25%):** FedEx, Delhivery — preferred for express and same-day orders; priority allocation for high-value shipments; eligible for volume bonuses.
- **Tier 2 (Delay Rate 25–27.5%):** DHL, Ecom Express, Blue Dart, Shadowfax — standard allocation for all modes; eligible for route-specific bonuses if zonal OTD exceeds 80%.
- **Tier 3 (Delay Rate > 27.5%):** Amazon Logistics, Ekart, XpressBees — standard and two-day mode preferred; reduced express allocation; required to submit improvement plans quarterly.

> **Note on Blue Dart:** Despite a moderate overall delay rate of 27.3%, Blue Dart has the highest count of long-severity delays (103 of 609 delayed = 16.9%). This requires monitoring and may warrant downgrading for time-sensitive shipments.

### 7.3 Partner-Specific SLA Targets

| Partner | OTD Target | Maximum Long-Severity % | Penalty Trigger |
| --- | --- | --- | --- |
| All Partners | > 73% | < 15% of delayed orders | OTD < 70% for 2 consecutive weeks |
| Tier 1 Partners | > 76% | < 12% of delayed orders | OTD < 73% for 2 consecutive weeks |
| Express Assignments | > 30% | < 20% of delayed orders | OTD < 25% for any week |

Table 15: Partner-specific SLA targets

---

## 8. Customer Experience Metrics

### 8.1 Tracking and Visibility

| Milestone Event | Update SLA | Customer Notification |
| --- | --- | --- |
| Order Assigned to Partner | Within 15 minutes | SMS + App notification |
| Picked Up from Warehouse | Within 30 minutes | App notification |
| In-Transit Update | Every 24 hours | App notification |
| Weather Delay Alert | Within 30 minutes of detection | SMS + App + Email |
| Out for Delivery | Within 1 hour | SMS + App + WhatsApp |
| Delivered / Failed | Within 15 minutes | SMS + App + Email |

Table 16: Real-time tracking update commitments

### 8.2 Customer Support Response Times

| Issue Type | First Response | Resolution SLA | Channels |
| --- | --- | --- | --- |
| Non-Delivery Complaint | 2 hours | 24 hours | Phone, Chat, Email |
| Damaged Product | 4 hours | 48 hours | Phone, Email, App |
| Wrong Item Delivered | 2 hours | 24 hours | Phone, Chat |
| Tracking Not Updated | 1 hour | 4 hours | Chat, App |
| Weather-Related Delay | 30 minutes | Automatic updates | SMS, App (automated) |
| Express Delivery Failure | 1 hour | 12 hours | Phone, Chat, Priority Queue |
| General Inquiry | 6 hours | 24 hours | All channels |

Table 17: Customer support SLA by issue type

### 8.3 Quality and Safety Metrics

- Damage rate: < 0.5% of total deliveries
- Lost package rate: < 0.2% of total shipments
- COD remittance accuracy: > 99.5%
- Delivery partner rating (customer feedback): > 4.0/5.0
- Package tampering incidents: Zero tolerance with immediate escalation
- Weather-related delay notification compliance: > 95% (must notify before delay occurs)

---

## 9. Incident Severity and Escalation

### 9.1 Delivery Incident Severity Definitions

| Severity | Definition |
| --- | --- |
| P1 — Critical | Partner network down; bulk shipment delays (>500 orders); hub fire/accident; complete zone outage; COD fraud detected; stormy weather halt affecting >200 orders |
| P2 — High | Significant route delays (>100 orders); partner capacity shortage; missed promised delivery date for high-value orders; tracking system failure; express OTD drops below 20% |
| P3 — Medium | Individual delivery failures; minor route delays (<50 orders); address issues; customer unavailability; 2nd/3rd delivery attempts; foggy weather slowdowns |
| P4 — Low | Tracking update delays; minor documentation issues; customer inquiries; delivery preference changes |

Table 18: Delivery incident severity classifications

### 9.2 Incident Response and Resolution Times

| Severity | Detection SLA | Partner Notified | Mitigation Action |
| --- | --- | --- | --- |
| P1 — Critical | 10 minutes | 15 minutes | Immediate rerouting; storm halt protocol |
| P2 — High | 30 minutes | 1 hour | Alternative partner in 2 hours |
| P3 — Medium | 2 hours | 4 hours | Redelivery scheduled |
| P4 — Low | 24 hours | As needed | Standard process |

Table 19: Incident detection and response commitments

### 9.3 Operations Coverage and Escalation

- Control Tower: 24x7 monitoring of all shipments and partner performance
- Partner Coordination: Dedicated POCs for each of 9 delivery partners, available 6 AM – 11 PM IST
- Customer Support: 24x7 helpline and chatbot; live agents 8 AM – 10 PM IST
- Escalation Path: Operations Executive → Shift Lead → Operations Manager → Regional Head → VP Supply Chain
- Contact Methods: Partner portal, dedicated Slack channels, hotline (+91-80-4500-7000), ops-control@company.in

---

## 10. Monitoring and Reporting

### 10.1 Real-Time Monitoring and Predictive Analytics

- GPS tracking updates every 10–15 minutes for in-transit shipments
- Delay prediction engine using ML models (scans partner velocity, weather, traffic, historical patterns)
- Automated alerts when predicted delay > 4 hours for any shipment
- Weather-integrated forecasting: ML model incorporates real-time weather data to predict delay probability per order
- Control tower dashboard with partner-wise, route-wise, region-wise, and weather-wise performance heatmaps
- Customer-facing tracking page with estimated delivery time and real-time location

### 10.2 Reporting and Performance Reviews

| Report Type | Frequency | Stakeholders |
| --- | --- | --- |
| Daily Operations Summary | Daily (8 AM) | Ops team, partners |
| Weather Impact Report | Daily + ad-hoc during severe weather | Ops team, dispatch |
| Partner Performance Scorecard | Weekly | Partner managers, ops leads |
| OTD and FADR Dashboard | Real-time | All stakeholders |
| Delay Root Cause Analysis | Weekly | Ops, analytics, partners |
| Mode-wise Performance Report | Weekly | Ops, planning |
| Customer Experience Report | Monthly | CX team, leadership |
| SLA Compliance and Penalty Report | Monthly | Finance, partners, leadership |
| Strategic Improvement Plan | Quarterly | Leadership, partners |

Table 20: Reporting and review cadence

### 10.3 Key Performance Indicators (KPIs)

| KPI | Target | Current Baseline |
| --- | --- | --- |
| Overall OTD | > 75% | 73.3% |
| Express OTD | > 40% | 26.1% |
| Same-Day OTD | > 75% | 67.5% |
| Standard OTD | > 99% | 100.0% |
| Two-Day OTD | > 98% | 99.6% |
| FADR (overall) | > 80% | — |
| MTTD (Mean Time to Detect Delay) | < 30 min | — |
| MTTM (Mean Time to Mitigate) | < 2 hours | — |
| Weather Notification Compliance | > 95% | — |
| Customer NPS | > 50 | — |
| Customer Complaint Rate | < 2% | — |
| Delivery Partner Rating | > 4.0/5.0 | — |
| COD Remittance Cycle Time | < 72 hours | — |
| Long-Severity Delay Rate | < 10% of delays | 11.9% |
| Cost per Delivery | Reduce 5% YoY | — |

Table 21: KPIs with targets and current baselines

---

## 11. Partner Penalties and Incentives

### 11.1 Penalty Structure for SLA Breaches

Penalties applied to delivery partners based on monthly performance:

| Performance Gap | Penalty (per order) |
| --- | --- |
| OTD 70–73% (Target: 73%) | ₹5 per delayed order |
| OTD 65–70% | ₹10 per delayed order |
| OTD < 65% | ₹20 per delayed order + formal review |
| Long-severity delays > 15% of total delays | ₹15 per long-severity order |
| FADR < 75% (Target: 80%) | ₹8 per re-attempt |
| Damage rate > 0.5% | ₹200 per damaged package |
| Lost package | ₹500 + product value |
| COD remittance delay > 72 hrs | 2% daily interest on outstanding |
| Weather notification failure | ₹3 per unnotified delayed order |

Table 22: Partner penalty schedule for performance breaches

### 11.2 Incentive Structure for Excellence

Partners exceeding SLA targets receive performance bonuses:

| Excellence Metric | Incentive |
| --- | --- |
| OTD > 78% overall | ₹2 bonus per order |
| Express OTD > 35% | ₹5 bonus per express order |
| FADR > 90% | ₹3 bonus per order |
| Zero long-severity delays in a week | ₹25,000 weekly bonus |
| Zero damage/lost packages (monthly) | ₹50,000 bonus |
| Customer rating > 4.5/5.0 | ₹25,000 bonus |
| 100% COD remittance compliance | 1% volume increase allocation |
| Storm-protocol compliance 100% | ₹10,000 per storm event |

Table 23: Partner incentive program for exceeding targets

### 11.3 Penalty and Incentive Reconciliation

- Performance measured and calculated weekly, finalized monthly
- Partner receives draft scorecard by 3rd of following month
- 5-day dispute window for partners to raise concerns with evidence
- Final scorecard published by 10th; penalties/incentives applied to invoice
- Consistent underperformance (3 consecutive months OTD < 68%) triggers partner review and potential removal

### 11.4 Customer Compensation for Delivery Failures

When delivery SLA is breached, customers receive automatic compensation:

| Failure Type | Customer Compensation |
| --- | --- |
| Express delivery delayed by 1+ days | Free shipping on next order |
| Same-day delivery delayed to next day | ₹100 voucher |
| Storm/weather delay (proactively notified) | ₹25 voucher as goodwill gesture |
| 3 failed delivery attempts (customer available) | ₹50 voucher + priority redelivery |
| Damaged product | Full refund + ₹200 voucher |
| Lost package | Full refund + 10% voucher |
| Wrong item delivered | Free return + expedited replacement |
| Long-severity delay (6+ hours) without notification | ₹150 voucher + priority support |

Table 24: Customer compensation for delivery SLA breaches

---

## 12. Routing Policy and Order Allocation

### 12.1 Multi-Partner Routing Logic

Orders are allocated to delivery partners using a weighted scoring algorithm:

| Factor | Weight | Description |
| --- | --- | --- |
| Partner OTD (last 7 days) | 30% | Recent on-time delivery performance |
| Partner Performance Tier | 20% | Tier 1/2/3 classification from Section 7 |
| Partner Capacity Availability | 20% | Real-time capacity vs. committed SLA |
| Cost per Delivery | 15% | Partner pricing for the route |
| Weather Suitability | 10% | Partner fleet composition vs. current weather |
| Customer Preference | 5% | Prior positive/negative delivery experience |

Table 25: Routing policy scoring factors and weights

### 12.2 Weather-Aware Routing Rules

- **Stormy weather:** Auto-assign to partners with highest van/truck fleet ratio. Do not assign express orders to partners with >60% two-wheeler fleet.
- **Rainy weather:** Prioritize partners with enclosed vehicles for electronics, documents, and fragile items.
- **Foggy weather:** Add 1-hour buffer to all express/same-day ETAs; reassign if partner's fog-zone OTD < 65%.

### 12.3 Dynamic Rerouting Rules

Real-time rerouting is triggered when:

- Predicted delay > 6 hours for any shipment (ML model triggers alert)
- Partner marks capacity exhausted for a zone
- Partner OTD drops below 65% in a 48-hour window for specific zone
- Weather alerts or traffic disruptions detected on primary route
- Hub/facility shutdown or operational issue reported by partner

Rerouting decisions are made within 30 minutes. Alternate partners are assigned automatically if backup capacity is available; otherwise, the issue is escalated manually to the Shift Lead.

### 12.4 Partner Selection Override Conditions

Routing algorithm can be manually overridden for:

- High-value orders (>₹25,000): Assign to Tier 1 partner (FedEx or Delhivery) with best FADR
- VIP/repeat customers: Assign to partner with highest rating for the customer's region
- Bulk B2B shipments: Negotiate dedicated capacity with specific partner
- Emergency same-day requirements: Use local hyperlocal partners even if costlier
- Severe weather: Override mode selection — auto-downgrade express to two-day

---

## 13. Roles and Responsibilities

### 13.1 Supply Chain Operations Team Responsibilities

- Operate 24x7 control tower monitoring all shipments and partner performance
- Run delay prediction models and trigger proactive mitigation for at-risk orders
- Monitor weather forecasts and activate weather-specific protocols
- Allocate orders to optimal partners using routing policy engine
- Manage partner capacity planning and surge preparedness
- Coordinate with partners on incident resolution and escalations
- Generate daily/weekly/monthly performance reports and conduct partner reviews
- Maintain SOP documentation, routing policies, and escalation runbooks
- Conduct root cause analysis for recurring delay patterns
- Implement continuous improvement initiatives based on pattern analysis

### 13.2 Delivery Partner Responsibilities

- Maintain agreed capacity levels and honor volume commitments
- Provide real-time tracking updates via API integration
- Ensure delivery personnel meet quality and safety standards
- Follow weather-specific vehicle assignment rules (Section 5.2)
- Attempt delivery at least twice before marking RTO (Return to Origin)
- Remit COD collections within 72 hours of delivery
- Report incidents and capacity constraints proactively
- Halt operations during stormy conditions per storm protocol
- Participate in weekly performance review calls
- Provide escalation contacts available 6 AM – 11 PM IST
- Comply with packaging, handling, and safety protocols
- Share forward-looking capacity forecasts for peak seasons

### 13.3 Customer Responsibilities

- Provide accurate and complete delivery address with landmark
- Ensure reachability on registered mobile number during delivery window
- Be available for delivery or authorize alternate recipient
- Inspect package and report damage immediately upon delivery
- Provide timely feedback on delivery experience
- Report non-delivery or issues within 24 hours via app/helpline

### 13.4 Shared Responsibilities (Operations + Partners)

- Capacity planning for seasonal peaks (Diwali, Prime Day, New Year sales)
- Disaster preparedness and alternate routing during natural calamities
- Weather monitoring and joint storm-response coordination
- Technology integration improvements (tracking API, auto-allocation, weather feeds)
- Cost optimization through route consolidation and zone redesign
- Customer experience improvement programs
- Fraud detection and prevention (fake delivery, COD defaults)

---

## 14. Data Privacy and Security

### 14.1 Security and Privacy Commitments

- Customer PII (name, address, phone) encrypted at rest and in transit
- Delivery partners access only assigned order details via secure API
- GPS tracking data retained for 90 days, then anonymized
- No sharing of customer data with third parties except delivery partners
- Compliance with India's Personal Data Protection Act (DPDPA) requirements
- Annual security audits of partner systems and processes
- Background verification mandatory for all delivery personnel

### 14.2 Fraud Prevention and COD Security

- Mandatory OTP verification for COD orders > ₹5,000
- Delivery partner photo proof required for all deliveries
- Real-time anomaly detection for suspicious delivery patterns (fake deliveries, address fraud)
- COD collection reconciliation within 72 hours; daily reconciliation for high-risk routes
- Tamper-evident packaging for high-value items
- Partner liability for COD defaults if delivery protocol not followed

---

## 15. Business Continuity and Disaster Management

### 15.1 Contingency Planning

| Disruption Scenario | Contingency Plan |
| --- | --- |
| Partner hub shutdown | Reroute via alternate partner hub in adjacent zone |
| Natural disaster (floods, cyclone) | Activate backup partners; auto-downgrade express/same-day; delay notifications to customers |
| Severe storm (>3 hours) | Halt all two-wheeler dispatches; reroute to van/truck; extend delivery windows by 6 hours |
| Strike or labor issues | Shift volume to alternate partners within 4 hours |
| Technology outage (tracking API) | Manual tracking via partner phone; restore within 2 hours |
| Surge demand (sale events) | Pre-negotiated surge capacity with all partners |

Table 26: Business continuity scenarios and response plans

### 15.2 Minimum Partner Requirements for BCP

- Maintain at least 3 active delivery partners per region
- Each partner must serve minimum 10% capacity to ensure competition
- No single partner to handle > 15% of overall volume (current max: Ekart at 11.3%)
- At least 2 partners must have >40% enclosed-vehicle fleet for weather resilience
- Quarterly BCP drills with simulated partner outage scenarios

---

## 16. SLA Review and Continuous Improvement

### 16.1 Review Schedule

- Monthly partner performance review meetings (every 1st Tuesday)
- Weekly weather impact review during monsoon season (June–September)
- Quarterly strategic review with leadership and partner account managers
- Annual comprehensive SLA refresh based on market benchmarks
- Ad-hoc reviews triggered by: 3 consecutive weeks OTD < 68%, major incidents, new partner onboarding
- Customer NPS and feedback incorporated into quarterly reviews

### 16.2 Continuous Improvement Framework

Based on delay pattern analysis and root cause data:

| Improvement Type | Action Trigger and Timeline |
| --- | --- |
| Short-term tactical fixes | Recurring delay pattern identified → implement quick fix within 1 week |
| Weather protocol update | New weather pattern causing >5% delay spike → protocol update within 3 days |
| Medium-term process changes | Root cause affects >5% of orders → SOP update within 1 month |
| Partner capability building | Specific partner consistently > 2pp above avg delay rate → training program |
| Long-term strategic initiatives | Systemic issue across zones → quarterly improvement project |
| Technology enhancements | Manual workaround used >50 times/week → automation within quarter |
| Fleet composition changes | Weather-related delays increasing → partner fleet upgrade plan within quarter |

Table 27: Improvement action framework based on issue severity and pattern

### 16.3 Improvement Priority Matrix for Recommendations

When generating improvement recommendations, prioritize in this order:

1. **Express delivery mode optimization** — highest delay rate (74%) with greatest business impact
2. **Weather-resilient operations** — stormy (42%), rainy (37%), foggy (31%) conditions cause disproportionate delays
3. **Long-distance routing** — 36% delay rate for >200 km; mode selection is key lever
4. **Partner performance equalization** — 3.2 percentage point spread between best (FedEx 24.6%) and worst (Amazon Logistics 28.1%)
5. **Vehicle-weather matching** — ensuring enclosed vehicles in adverse weather
6. **Severity reduction** — targeting the 12% long-severity delays for maximum customer impact reduction
7. **Package-specific handling** — ensuring fragile, pharmacy, and electronics get weather-appropriate transport

### 16.4 Amendment Process

- SLA target adjustments proposed based on 6-month trend analysis
- Partner input period of 15 days for feedback and negotiation
- Changes require sign-off from VP Supply Chain and partner account leads
- Updated SLA effective first day of following month with 30-day notice

---

## 17. Escalation Procedures

### 17.1 Operational Escalation Path

- Level 1 — Ops Executive (Control Tower): Incident detection, initial triage, partner notification (0–30 minutes)
- Level 2 — Shift Lead: Rerouting decisions, alternate partner assignment, customer communication (30 min – 2 hours)
- Level 3 — Operations Manager: Multi-partner coordination, capacity sourcing, policy exceptions (2–4 hours)
- Level 4 — Regional Head: Zone-wide disruptions, partner negotiations, major incident command (4+ hours)
- Level 5 — VP Supply Chain: Strategic partner issues, SLA renegotiation, crisis management

### 17.2 Escalation Triggers

Automatic escalation occurs when:

- P1 incident (>500 orders delayed or partner network down) detected
- Any shipment with predicted delay > 12 hours (long-severity threshold)
- Partner OTD drops below 65% in any 24-hour period
- Customer escalation (social media, executive complaint)
- High-value order (>₹50,000) delayed beyond promise date
- Same order fails delivery 3 times with customer available
- Storm warning affecting > 3 partner hubs simultaneously

### 17.3 Escalation Contacts

| Role | Name | Contact |
| --- | --- | --- |
| Control Tower Lead | Amit Verma | +91-98765-11001, amit.v@company.in |
| Operations Manager (North) | Sneha Iyer | +91-98765-11002, sneha.i@company.in |
| Operations Manager (South) | Rajesh Nair | +91-98765-11003, rajesh.n@company.in |
| Operations Manager (East) | Kavita Das | +91-98765-11004, kavita.d@company.in |
| Operations Manager (West) | Rohan Patil | +91-98765-11005, rohan.p@company.in |
| Operations Manager (Central) | Meera Joshi | +91-98765-11006, meera.j@company.in |
| Regional Head (India) | Priya Mehta | +91-98765-11010, priya.m@company.in |
| VP Supply Chain | Vikram Singh | +91-98765-11020, vikram.s@company.in |

Table 28: Operational escalation contact directory (all 5 regions)

---

## 18. Definitions and Glossary

- **OTD (On-Time Delivery):** Percentage of orders delivered by promised date
- **FADR (First Attempt Delivery Rate):** Percentage of orders delivered on first attempt
- **COD (Cash on Delivery):** Payment collected at delivery by partner
- **RTO (Return to Origin):** Shipment returned to warehouse after failed delivery attempts
- **NDR (Non-Delivery Report):** Report filed when delivery attempt fails
- **In-Transit:** Shipment picked up and moving toward destination
- **Out for Delivery:** Shipment loaded on delivery vehicle for final leg
- **Delay Prediction:** ML-based forecast of delivery time using real-time partner velocity, weather, and historical patterns
- **Control Tower:** Centralized 24x7 operations monitoring and coordination center
- **Partner Scorecard:** Weekly/monthly performance report for each delivery partner
- **Region:** Geographic delivery area — North, South, East, West, Central
- **Hub:** Partner sorting and distribution facility
- **Last-Mile:** Final delivery leg from local hub to customer address
- **Short-Severity Delay:** 1–2 hour delay beyond promised delivery time
- **Medium-Severity Delay:** 3–5 hour delay beyond promised delivery time
- **Long-Severity Delay:** 6+ hour delay beyond promised delivery time
- **Schedule Risk Score:** Numeric score (0–12) indicating delivery time pressure; higher = more at-risk
- **Vehicle Load Strain:** Ratio of actual cargo weight to vehicle capacity; values >0.8 trigger overload alert
- **Storm Protocol:** Mandatory operational procedures activated during stormy weather conditions

---

## 19. Agreement Acceptance

This Service Level Agreement is effective as of February 1, 2026 and remains in effect for 12 months, with monthly performance reviews and quarterly strategic reviews.

### Supply Chain Operations (Company)

Signature: ________________________
Name: Vikram Singh
Title: VP, Supply Chain
Date: February 1, 2026

### Partner Account Management (Company)

Signature: ________________________
Name: Priya Mehta
Title: Regional Head, Partner Operations
Date: February 1, 2026

### Delivery Partner Representative (Delhivery)

Signature: ________________________
Name: Arjun Malhotra
Title: Head of Enterprise Accounts
Date: February 1, 2026

### Delivery Partner Representative (Blue Dart)

Signature: ________________________
Name: Sunita Desai
Title: VP, Strategic Partnerships
Date: February 1, 2026

(Similar sign-off from Amazon Logistics, DHL, Ecom Express, Ekart, FedEx, Shadowfax, and XpressBees)

---

## Appendix A: Active Delivery Partner Network

| Partner | Coverage | Specialization | Approx. Volume Share | Historical Delay Rate |
| --- | --- | --- | --- | --- |
| Amazon Logistics | Pan-India | Express, Standard | ~10.8% | 28.1% |
| Blue Dart | Tier 1/2 cities | Express, Same-day | ~11.2% | 27.3% |
| Delhivery | Pan-India | All modes | ~11.2% | 24.9% |
| DHL | Tier 1/2 cities | Express, High-value | ~11.0% | 26.0% |
| Ecom Express | Tier 1/2/3 | Standard, COD | ~10.9% | 26.4% |
| Ekart | Pan-India | Standard, Two-day | ~11.3% | 27.8% |
| FedEx | Tier 1/2 cities | Express, Premium | ~11.2% | 24.6% |
| Shadowfax | Metro + Tier 2 | Same-day, Hyperlocal | ~11.0% | 27.3% |
| XpressBees | Pan-India | Standard, COD | ~11.3% | 27.7% |

Table A1: Current partner network, capacity allocation, and performance

---

## Appendix B: Region-wise Performance Benchmarks

Based on historical analysis of 20,000 deliveries:

| Region | Total Orders | Delayed | On-Time | Delay Rate | Avg Distance (km) | Schedule Risk |
| --- | --- | --- | --- | --- | --- | --- |
| North | 3,963 | 1,051 | 2,912 | 26.5% | 149.3 | 4.64 |
| South | 3,940 | 1,056 | 2,884 | 26.8% | 148.7 | 4.56 |
| East | 3,933 | 1,020 | 2,913 | 25.9% | 150.2 | 4.53 |
| West | 4,113 | 1,118 | 2,995 | 27.2% | 151.9 | 4.59 |
| Central | 4,051 | 1,090 | 2,961 | 26.9% | 152.0 | 4.69 |

Table B1: Regional performance summary

> **Insight:** Regional delay rates are tightly clustered (25.9%–27.2%), indicating that delays are driven more by weather and mode than by geography alone. However, Central and West regions show slightly elevated schedule risk scores, suggesting more complex routing.

---

## Appendix C: Delivery Mode Deep Dive

| Mode | Total Orders | Delayed | On-Time | Delay Rate | Short | Medium | Long | Avg Schedule Risk |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Express | 4,979 | 3,679 | 1,300 | 73.9% | 1,934 | 1,536 | 517 | 5.51 |
| Same Day | 5,036 | 1,637 | 3,399 | 32.5% | 766 | 583 | 118 | 7.30 |
| Standard | 4,991 | 0 | 4,991 | 0.0% | 0 | 0 | 0 | 1.86 |
| Two Day | 4,994 | 19 | 4,975 | 0.4% | 0 | 0 | 0 | 3.72 |

Table C1: Historical delivery mode performance breakdown

> **Critical Finding:** Express mode alone accounts for **69% of all delayed orders** (3,679 of 5,335) and **81% of all long-severity delays** (517 of 635). Any recommendation to reduce overall delay rate must focus on express mode as the primary lever.

---

## Appendix D: High-Risk Combination Matrix

Top 15 highest-risk factor combinations from historical data:

| Rank | Combination | Orders | Delayed | Delay Rate | Risk Level |
| --- | --- | --- | --- | --- | --- |
| 1 | Express + Stormy | 834 | 832 | 99.8% | Critical |
| 2 | Express + Rainy | 829 | 808 | 97.5% | Critical |
| 3 | Express + Foggy | 824 | 737 | 89.4% | Critical |
| 4 | Express + Long Distance | 1,660 | 1,294 | 78.0% | Critical |
| 5 | Express + Medium Distance | 2,484 | 1,842 | 74.2% | Critical |
| 6 | Same Day + Stormy | 852 | 556 | 65.3% | Critical |
| 7 | Express + Short Distance | 835 | 543 | 65.0% | Critical |
| 8 | Same Day + Long Distance | 1,690 | 1,072 | 63.4% | Critical |
| 9 | Express + Cold | 812 | 430 | 53.0% | Critical |
| 10 | Express + Hot | 859 | 449 | 52.3% | Critical |
| 11 | Express + Clear | 821 | 423 | 51.5% | Critical |
| 12 | Same Day + Rainy | 801 | 417 | 52.1% | Critical |
| 13 | Same Day + Foggy | 844 | 294 | 34.8% | Medium |
| 14 | Long Distance overall | 6,644 | 2,385 | 35.9% | High |
| 15 | Foggy overall | 3,386 | 1,031 | 30.5% | Medium |

Table D1: Top 15 high-risk factor combinations

---

## Appendix E: Common Delay Root Causes and Quick Actions

Based on historical pattern analysis (Oct 2025 – Jan 2026):

| Root Cause | Frequency | Quick Action (Short-term) | Long-term Mitigation |
| --- | --- | --- | --- |
| Express mode inherent scheduling pressure | 35% | Add buffer time; auto-downgrade option | Redesign express SLA with weather-adjusted windows |
| Adverse weather (rainy/stormy) | 22% | Switch to enclosed vehicles; halt if severe | Weather-predictive dispatch; seasonal fleet adjustment |
| Long-distance routing | 18% | Prefer standard/two-day for >200 km | Zone-based hub network expansion |
| Hub capacity exhaustion | 10% | Reroute via alternate hub; temporary capacity | Capacity planning model; peak-season pre-allocation |
| Address incomplete/incorrect | 6% | Auto-call customer; update address in system | Address verification at order placement |
| Customer unavailable | 5% | Reschedule via SMS; offer alternate time slot | Preferred delivery window feature |
| Vehicle breakdown | 3% | Reassign to backup delivery executive | Preventive maintenance schedule; fleet modernization |
| Partner staffing shortage | 1% | Shift volume to alternate partner | Multi-partner redundancy per zone |

Table E1: Delay root causes with short-term and long-term mitigation strategies

---

## Appendix F: Severity Distribution by Key Factors

### F.1 Severity by Weather

| Weather | Total Delayed | Short (1–2h) | Medium (3–5h) | Long (6+h) | % Long |
| --- | --- | --- | --- | --- | --- |
| Clear | 556 | 501 | 51 | 3 | 0.5% |
| Cold | 530 | 419 | 68 | 1 | 0.2% |
| Hot | 586 | 512 | 63 | 4 | 0.7% |
| Foggy | 1,031 | 775 | 335 | 37 | 3.6% |
| Rainy | 1,227 | 387 | 724 | 185 | 15.1% |
| Stormy | 1,405 | 106 | 878 | 405 | 28.8% |

Table F1: Severity distribution by weather condition

> **Key Insight:** Stormy weather produces 28.8% long-severity delays (vs. 0.5% in clear weather). Rainy weather produces 15.1% long-severity. These two conditions account for **93% of all long-severity delays** (590 of 635).

### F.2 Severity by Distance

| Distance | Total Delayed | Short | Medium | Long | % Long |
| --- | --- | --- | --- | --- | --- |
| Short (<50 km) | 562 | 182 | 197 | 21 | 3.7% |
| Medium (50–200 km) | 2,388 | 1,354 | 913 | 177 | 7.4% |
| Long (>200 km) | 2,385 | 1,164 | 1,009 | 437 | 18.3% |

Table F2: Severity distribution by distance category

---

## Appendix G: Seasonal and Event-Based Guidelines

### G.1 Monsoon Season (June–September)

- Rainy and stormy weather frequency increases 3–4x during monsoon
- Express mode OTD expected to drop below 20% during heavy monsoon weeks
- **Mandatory actions:** Increase enclosed vehicle allocation by 40%; halt express dispatch during heavy rain advisories; proactively notify customers at order placement about potential delays; increase partner capacity agreements by 20%

### G.2 Winter Fog Season (December–February)

- Foggy conditions prevalent in North and Central regions
- Express + Foggy: 89% delay rate — significant impact
- **Mandatory actions:** Add 2-hour buffer to all express ETAs in North/Central during fog advisories; prioritize Tier 1 partners for express; deploy additional van/truck fleet

### G.3 Peak Sale Events (Diwali, Prime Day, New Year, Republic Day)

- Order volume spikes 3–5x during major sale events
- All partners must commit surge capacity 30 days in advance
- Express and same-day modes may be suspended if partner OTD drops below 60% during event
- Dedicated escalation hotline activated during events

### G.4 Extreme Heat (April–June)

- Hot weather delay rate is moderate at 17.5%
- **Special attention:** Pharmacy (temperature-sensitive medications), cosmetics (melting risk), groceries (perishables)
- **Mandatory actions:** Cold-chain vehicles for pharmacy during temperatures >40°C; same-day priority for grocery orders; heat-sensitive package labeling

---

## Appendix H: Contact Information

- Control Tower (24x7): +91-80-4500-7000
- Partner Coordination Slack: #delivery-partners-ops
- Weather Alert Channel: #weather-ops-alerts
- Storm Protocol Activation: ops-storm@company.in
- Escalation Email: escalation@company.in
- Partner Onboarding: partner-ops@company.in
