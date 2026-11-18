<p align="center">
  <img src="custom_components/house_observer/brand/icon@2x.png" alt="House Observer icon" width="192">
</p>

# House Observer for Home Assistant

House Observer converts selected Home Assistant telemetry into a small, local,
operational memory for a property. It records meaningful state transitions,
learns simple explainable baselines, generates daily or on-demand summaries,
and can call any configured Home Assistant AI Task provider for interpretation.

Each property runs in one of two modes:

- **Home** watches for overuse and misbehaving equipment: high or unusual water
  use, sustained power draw, appliances that run too long or cycle oddly,
  heating or cooling fighting an open door, and things left open or unlocked.
- **Short-term rental** does the same, and also changes focus with occupancy.
  While guests stay it looks for property misuse, such as doors held open with
  the heat running, the hot tub cover left off, extreme setpoints, quiet-hours
  activity, or use far beyond typical stays. While the house is empty it looks
  for anything that needs fixing before the next arrival, such as an open door,
  window, garage, or hot tub cover, water flow, or equipment left running.

> [!IMPORTANT]
> House Observer is an analyst, not a safety controller. Keep deterministic Home
> Assistant automations for smoke, carbon monoxide, freezing, leaks, security,
> equipment limits, and other time-critical conditions.

## Example output

A representative summary returned by `house_observer.generate_summary`:

```yaml
timestamp: "2026-09-03T18:18:43.653031-04:00"
reason: manual
period_hours: 24
summary: >-
  The home shows apparent occupancy activity on the main and kitchen floors.
  Whole-house power was elevated from 17:22–17:55, averaging 1,700 W—slightly
  below the 1,815 W baseline—and spiked again from 18:06–18:11 to approximately
  2,100–2,200 W. The spike was driven primarily by a sustained increase on the
  freezer/server/bathroom circuit to 580 W, compared with a 215 W baseline. The
  circuit returned to approximately 150 W by 18:17, and whole-house power was
  declining. Room-level presence sensors remained away. No safety or device
  faults were detected.
severity: watch
confidence: 0.68
observations: |-
  Main-floor and kitchen occupancy sensors show occupied; individual room sensors remain away.
  The freezer/server/bathroom circuit sustained approximately 580 W from 18:06–18:11.
  Whole-house power averaged 1,700 W from 17:22–17:55, below its 1,815 W baseline.
  The living-room AC is cooling toward its 74 °F target; other climate units are idle.
  The media-cabinet lock has been unlocked since 17:26; other access points are normal.
anomalies: |-
  Freezer/server/bathroom circuit reached 2.7 times its baseline from 18:06–18:11,
  unexplained by observed occupancy or reported device operation.
maintenance_notes: |-
  Confirm the freezer/server/bathroom circuit's expected load. If the high draw
  recurs, inspect the connected equipment and circuit.
candidate_memories: |-
  Whole-house power has an afternoon/evening baseline near 1,815 W; readings above
  2,200 W warrant checking the freezer/server/bathroom circuit.
notify_owner: false
ai_generated: true
```

## What it does

- Watches one effective entity set: **discovered + pinned − ignored**.
- Lets an AI Task provider pick a small useful set from an area-grouped
  inventory, re-reviewing it after 1, 3, and 7 days and then weekly.
- Resolves occupancy from an occupancy entity, a supplied stay, or booking
  calendars, and tailors the analysis to the mode and occupancy.
- Tells the model how long each entity has held its state, so "the hot tub
  cover has been open for 4 hours" is visible, not inferred.
- Learns explainable numeric baselines per part of the day (night, morning,
  afternoon, evening). Older readings fade over about a month.
- Gives the model a compact **house log** of the whole lookback window instead
  of raw rows: every door, lock, appliance, and occupancy change as intervals
  with durations, numeric sensors as change-point segments, thermostat mode,
  action, and setpoint changes, open-door-while-heating overlaps, and a
  timeline. A busy day of about 8,000 recorded changes fits in roughly 5,000
  tokens.
- Registers read-only **House Observer tools** for conversation agents, so an
  agent can pull raw readings for any monitored entity and time range.
- Keeps owner notes and confirmed patterns as **property knowledge** that every
  summary sees, and passes recent summaries forward for continuity.
- Produces a daily summary at a configurable time and on-demand summaries with
  structured response data.
- Falls back to a deterministic summary, including anything open or unlocked
  for 30+ minutes, when AI Task is unavailable.
- Reports failures on the Status sensor and as a Repairs issue after two
  consecutive failures.

## Installation with HACS

Until this project is part of HACS's default catalog, add it as a custom
repository:

1. Open HACS in Home Assistant.
2. Open the menu and choose **Custom repositories**.
3. Add `https://github.com/karlg100/ha-house-observer`.
4. Select **Integration** as the category.
5. Install **House Observer** and restart Home Assistant.
6. Open **Settings > Devices & services > Add integration** and search for
   **House Observer**.

Setup asks for the property name and type, then what to watch, how to
summarize, and how to alert. Afterwards, **Configure** opens a menu with the
same four sections: What to watch, Summaries and property type, Alerts, and
Learning and retention.

Keep **Learning-only mode** on through several representative days or stays.
Summaries are still produced; only anomaly alerts are held back.

## Choosing what to watch

The monitored set is built in one place, in this order:

1. **Discovery** recommendations, when automatic discovery is on.
2. **Pins**: *Always monitor these entities/devices* in the options, or the
   Home Assistant label `observer` on an entity or device. A pinned device
   contributes every useful entity it has.
3. **Ignores**: *Never monitor these entities/devices*, or the label
   `observer_ignore`. Ignores always win.

Labels named `observer_` followed by a category both pin an entity and set its
category, for example `observer_water`, `observer_spa`, or `observer_access`.
Create a label called "Observer water" and Home Assistant gives it the ID
`observer_water`. Categories are: access, activity, occupancy, hvac, water,
energy, appliance, spa, network, reservation, and other. Label changes apply
within seconds; discovery re-reviews an hour after registry changes settle.

Discovery inventories eligible entities by area. Cameras, images, people,
device trackers, phones, scripts, and automations are excluded. Between reviews
only change counts are kept. The **Monitored entities** sensor lists the
recommendations with reasons, the next review, and any discovery error.

### Occupancy

- **Occupancy entity** (both modes): a helper, binary sensor, zone, or counter.
  `on`, `home`, or a count above zero means occupied. For a rental this is
  usually a guest-mode helper; for a home, `zone.home` works well.
- **Stay** (rental): set with `house_observer.set_stay_context`. It clears
  itself after check-out.
- **Reservation calendars** (rental): calendars or sensors with start and end
  times.

The first available source wins. With none, the model is told occupancy is
unknown and to say which it assumed.

## AI Task setup

House Observer does not include an AI model. Install a Home Assistant
integration that supplies an AI Task entity, and select it under **Summaries**,
or leave the field blank to use Home Assistant's preferred one.

## The house log

Before each summary, House Observer reads the recorder history of every
monitored entity for the lookback window and renders it as text:

```text
Sliding Door: closed Mon 20:49-07:12 (10h23) · open 07:12-07:14 (2m) · closed …
House Power (W): … 16:52-16:57 1.24k..1.36k · 18:22-18:26 2.09k..2.36k · …
Main Floor [mode/action/setpoint]: heat/idle/69.5 … · heat/heating/69.5 17:30-18:30 (1h)
Porch door open while Main Floor heating: Tue 17:40-17:52 (12m)
```

- Discrete states keep every change, including momentary ones.
- A numeric segment ends when the value leaves the sensor's normal variation,
  judged from its learned spread, so a 7 W boiler and a 2 kW house are each
  read on their own scale and short spikes keep their own segment.
- If a log ever exceeds its budget, the timeline is dropped first, then numeric
  tolerance is widened. Discrete intervals are never dropped.

Without the recorder, the log is built from House Observer's own retained
observations.

## Letting an agent dig deeper

House Observer registers a Home Assistant LLM API named **House Observer**
with four read-only tools: list monitored entities, get the house log for any
range up to 7 days, get raw readings for one monitored entity, and get recent
summaries and notes. Tools only read monitored entities.

To use them:

1. In your AI integration (for example Anthropic), add or edit a conversation
   agent and select **House Observer** under its Home Assistant LLM API
   setting. A dedicated agent without **Assist** keeps it from controlling
   devices.
2. In House Observer, choose that agent as the **Analysis agent** under
   **Summaries and property type**.

Summaries then run through the agent, which reads the house log and can call
the tools for detail. If the agent fails or does not return the summary JSON,
the AI Task entity is used instead, and the error is shown on the Status
sensor as `analysis_agent_error`. The Last summary sensor's `analysis_path`
shows which was used. The same tools also let you ask that agent things like
"when was the garage open yesterday?"

## Persistent observer guidance

**Summaries > Persistent observer guidance** is included with every AI
summary. For example:

```text
Pay particular attention to whole-house power between 4:00 PM and 8:00 PM.
Report sustained high demand, when it occurred, and how long it lasted.
Do not treat a single short spike as an anomaly.
```

Guidance focuses the analysis but cannot override the evidence, privacy,
safety, or notification rules.

## Entities

One House Observer device is created per property:

- **Status**: `learning`, `active`, or `error`, with `last_error` attributes.
- **Condition**: the latest summary's severity: `normal`, `note`, `watch`, or
  `action`. Unlike earlier versions, learning mode does not hide it.
- **Occupancy**: `occupied`, `vacant`, or `unknown`, with its source and, for
  rentals, the next arrival.
- **Last summary**: timestamp, with the full summary in attributes.
- **Property notes**: count of saved notes, listed with their IDs.
- **Monitored entities**, **Events in 24 hours**, **Anomalies in 24 hours**,
  and **Learned baselines**: diagnostics.

Large text attributes are excluded from the recorder database.

## Actions

### Generate a summary

```yaml
action: house_observer.generate_summary
data:
  hours: 24
response_variable: observer_result
```

### Discover important entities now

```yaml
action: house_observer.discover_entities
response_variable: discovery_result
```

### Record a note or confirm a pattern

Notes are property knowledge that every future summary sees, unlike the event
history, which ages out.

```yaml
action: house_observer.record_note
data:
  category: maintenance
  note: Replaced the spa filter and changed filtration settings.
```

Summaries propose `candidate_memories`. Ones that recur are offered back to the
model as unconfirmed patterns. To confirm one, save it with
`category: property_pattern`. Add `expires_in_days` for temporary context, such
as a guest report. Remove a note with `house_observer.forget_note` and the
`note_id` shown on the **Property notes** sensor.

### Set a stay (rental)

```yaml
action: house_observer.set_stay_context
data:
  guest_count: 8
  pet_count: 2
  check_in: "2026-09-04 16:00:00"
  check_out: "2026-09-07 10:00:00"
```

`reservation_id` and `label` are accepted and kept locally for your own use,
but never sent to the AI provider. Pass `clear: true` to end a stay early.

If you configure more than one property, include `config_entry_id` in action
calls.

## Notifications

Enter a notify entity or action, such as `notify.mobile_app_karl_phone`, under
**Alerts**. Daily summaries are independently optional.

Anomaly notifications require all of the following:

1. Learning-only mode is off.
2. A notification target is configured.
3. The AI classifies the condition as `watch` or `action`.
4. The AI explicitly sets `notify_owner` to true.
5. The 30-minute anomaly cooldown has elapsed.

## Memory and privacy

- Event history, baselines, and notes stay in the Home Assistant instance.
- Only the monitored set retains state transitions, with a small allowlist of
  attributes.
- Booking data is reduced to locally calculated timing and guest and pet
  counts. Guest names, booking IDs, labels, contact details, access codes, and
  calendar URLs never reach the AI provider or the retained events.
- Event retention defaults to 45 days and 5,000 events. Baselines and notes
  survive pruning.
- Prompts prohibit identities, motives, protected characteristics, and head
  counts, and describe people only through property evidence.

Selected telemetry is sent to the configured AI Task provider when a summary or
discovery runs. The provider's own privacy terms apply.

## Upgrading from 0.2

The upgrade is automatic:

- The old per-category pickers become pins, and their categories are kept.
- Properties with reservation entities become rentals; others become homes.
  Change this under **Summaries and property type**.
- Owner notes move from the event history into property knowledge.
- Stored memory is migrated once, instead of being re-scrubbed at every start.
- The Status sensor now reports health. Use the new **Condition** sensor in
  automations that used to read the severity from Status.

## Known limits

- Numeric deviation uses decaying mean and standard deviation per part of the
  day. It is a transparent clue, not a fault diagnosis.
- Weather-normalized spa recovery and cross-sensor energy correlation are not
  implemented.

## Development

Pure-logic tests need only `pytest`. The integration tests in
`tests/integration` run against a real Home Assistant instance with a recorder
and are skipped unless the packages in `requirements_test.txt` are installed.

```bash
python -m pytest
python scripts/validate_repo.py
```

## License

MIT
