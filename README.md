<p align="center">
  <img src="custom_components/house_observer/brand/icon@2x.png" alt="House Observer icon" width="192">
</p>

# House Observer for Home Assistant

House Observer converts selected Home Assistant telemetry into a small, local,
operational memory for a property. It records meaningful state transitions,
learns simple explainable baselines, generates daily or on-demand summaries,
and can call any configured Home Assistant AI Task provider for interpretation.

The integration is designed for any Home Assistant installation where raw
history is plentiful but useful operational context is hard to see. Optional
occupancy and reservation context also makes it useful for short-term rental
operations without making rentals a requirement.

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

## Current status

House Observer includes persistent owner guidance for AI summaries, a
progressive entity-discovery schedule, deterministic binary-sensor semantics,
and privacy-safe reservation timing. Learning-only mode is enabled by default,
and proactive anomaly notifications remain suppressed until you explicitly
disable it.

## What it does

- Watches only the effective entity set selected manually, recommended by
  discovery, or required by user overrides.
- Inventories eligible entities by Home Assistant area and device, then asks
  the configured AI for a minimal useful set.
- Groups entities by operational meaning: activity, access, occupancy, spa,
  HVAC, Internet, energy, and optional schedule context.
- Debounces high-frequency numeric telemetry before storing it.
- Retains a bounded local event history in Home Assistant `.storage`.
- Learns per-entity numeric ranges, state frequency, and activity by hour.
- Marks statistically unusual numeric readings as explainable deviation
  candidates after enough samples exist.
- Produces a daily summary at a configurable local time.
- Produces manual summaries through the `house_observer.generate_summary`
  action and returns structured response data.
- Uses a configured `ai_task` entity, or Home Assistant's preferred AI Task
  entity, without tying the integration to one AI vendor.
- Includes optional persistent owner guidance with every AI summary.
- Falls back to a deterministic summary when AI Task is unavailable.
- Stores optional schedule context without creating a permanent personal profile.
- Normalizes configured reservation start/end times locally, including exact
  day and minute offsets, before they reach the AI provider.
- Converts binary sensor states into device-class-aware meanings before AI
  analysis, such as `problem: off` becoming `normal` rather than “powered off.”
- Exposes status, recent event/anomaly counts, learned baseline count, active
  optional occupancy context, and latest summary sensors.

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

## Recommended first configuration

Start with a deliberate subset of reliable entities. Good first candidates
for many Home Assistant installations are:

| Category | Useful entity types |
| --- | --- |
| Activity | Motion, appliance operation, meaningful room activity helpers |
| Access | Exterior door contacts and lock state |
| Occupancy | Non-camera presence or occupancy helpers |
| Spa | Water temperature, setpoint, heater, pumps, filtration, ozone, power |
| HVAC | Climate entities, room temperature, HVAC action, mini-split power |
| Network | WAN availability, Starlink latency/outages, router availability |
| Energy | Whole-home power and important Emporia circuits |
| Schedule context | Optional calendar or helpers describing a relevant period |

Keep **Learning-only mode** enabled through several representative days or
operating cycles. Review the stored summaries and baseline candidates before
enabling proactive alerts.

To use automatic discovery, leave the manual category fields blank and enable
**Automatically discover important devices**. The first discovery
runs shortly after setup. Successful AI discovery is then repeated after about
24 hours, on day 3, and on day 7 before settling into the configured interval,
which is weekly by default. Failed AI discovery attempts retry daily.

## Automatic device discovery

Discovery is deliberately separate from anomaly detection and summary writing:

1. Home Assistant registries provide entity, device, and area metadata.
2. A local filter excludes private or unhelpful domains such as people, device
   trackers, cameras, images, scripts, and automations.
3. Between reviews, the Observer stores only a change count for each eligible
   candidate. It does not retain the candidate's raw state-change history.
4. The configured AI Task provider reviews a bounded inventory grouped by area
   and recommends the smallest useful set of entities.
5. The Observer validates every returned entity ID against the supplied
   inventory before monitoring it.

User overrides always take precedence:

- **Always monitor these devices/entities** adds operational signals even when
  the AI does not select them.
- **Never monitor these devices/entities** removes signals even when they were
  manually selected or recommended by the AI.

Recommendations and reasons appear on the **Discovered devices** diagnostic
sensor. You can also run discovery immediately from **Settings > Tools >
Actions** with `house_observer.discover_entities`.

The **Discovered devices** sensor also shows the next scheduled review and the
current schedule phase. Entity, device, or area registry changes queue a fresh
review after a one-hour settling period, so newly added equipment can be
considered without waiting for the regular interval.

## AI Task setup

House Observer does not include an AI model or require a particular provider.
Install and configure a Home Assistant integration that supplies an AI Task
entity. Select that entity in House Observer settings, or leave the field blank
to use Home Assistant's preferred AI Task entity.

When no `ai_task.generate_data` action is available, House Observer records a
deterministic summary containing event and deviation counts instead.

## Persistent observer guidance

Open **Settings > Devices & services > House Observer > Configure** and use
**Persistent observer guidance** for property-specific analysis priorities.
The text is included with every AI-generated summary and remains configured
until you edit or remove it. Clear the field and submit the form to remove the
guidance.

For example:

```text
Pay particular attention to whole-house power usage between 4:00 PM and
8:00 PM local time. Report sustained high demand, when it occurred, how long
it lasted, and whether the supplied evidence differs meaningfully from learned
behavior. Do not treat a single short spike as an anomaly.
```

Guidance focuses the model's analysis but does not override House Observer's
evidence, privacy, safety, or notification rules. The status sensor and
downloadable diagnostics indicate whether guidance is configured without
exposing its contents.

## Entities

One House Observer device is created per configured property with these sensors:

- **Status**: `learning`, `normal`, `note`, `watch`, or `action`.
- **Events in 24 hours**: number of curated state transitions.
- **Anomalies in 24 hours**: number of numeric baseline deviation candidates.
- **Learned baselines**: entities with enough observations to compare.
- **Last summary**: timestamp with structured summary attributes.
- **Active stay**: optional manually supplied occupancy or schedule context.

## Actions

### Generate a summary

```yaml
action: house_observer.generate_summary
data:
  hours: 24
response_variable: observer_result
```

The response includes `summary`, `severity`, `confidence`, `observations`,
`anomalies`, `maintenance_notes`, `candidate_memories`, `notify_owner`, and
metadata about when and why it was generated.

### Discover important devices now

```yaml
action: house_observer.discover_entities
response_variable: discovery_result
```

The response lists the areas, devices, entity IDs, operational categories, and
reasons selected by the AI. When AI Task is unavailable or returns no valid
IDs, a conservative local fallback is used.

### Record a property note

Use this after maintenance, an equipment change, or any reported issue so future
summaries have relevant context.

```yaml
action: house_observer.record_note
data:
  category: maintenance
  note: Replaced the spa filter and changed filtration settings.
```

### Set optional occupancy context

```yaml
action: house_observer.set_stay_context
data:
  reservation_id: ABC123
  label: Scheduled occupancy
  guest_count: 8
  pet_count: 2
  check_in: "2026-09-04 16:00:00"
  check_out: "2026-09-07 10:00:00"
```

Clear it when the context no longer applies:

```yaml
action: house_observer.set_stay_context
data:
  clear: true
```

If you configure more than one property, include `config_entry_id` in action
calls. The UI action editor provides a config-entry picker.

## Notifications

Enter a full notification action such as `notify.mobile_app_karl_phone` in the
integration options. Daily summaries are independently optional.

Anomaly notifications require all of the following:

1. Learning-only mode is disabled.
2. A configured notification action exists.
3. The AI response classifies the condition as `watch` or `action`.
4. The AI response explicitly sets `notify_owner` to true.
5. The anomaly-analysis cooldown has elapsed.

These rules prevent a model response alone from bypassing the integration's
notification policy.

## Memory and privacy

- Event history and learned aggregates remain in the Home Assistant instance.
- Only the effective monitored set retains state transitions.
- Automatic discovery retains candidate change counts locally between reviews.
- Discovery sends a bounded, area-grouped inventory to the configured AI Task
  provider only when a discovery review runs.
- A deliberately small allowlist of state attributes is retained.
- Reservation states are replaced with locally calculated, schedule-only
  context. Guest names, booking numbers, contact details, access codes, notes,
  and private calendar URLs are excluded from AI prompts and retained events.
- Cameras, images, people, device trackers, audio, scripts, and automations are
  excluded from automatic discovery.
- Event retention defaults to 45 days and is capped at 5,000 events.
- Aggregate patterns survive event pruning so the property can learn over time.
- AI prompts explicitly prohibit identities, motives, protected
  characteristics, or unsupported occupancy conclusions.
- Candidate memories describe the property, not individuals.

Selected telemetry is sent to the configured AI Task provider whenever an AI
summary is generated. The provider's own privacy and retention terms apply.

## Known limits

- Baselines are per entity. Weather-normalized spa recovery and cross-sensor
  energy correlation are planned, but not yet implemented.
- Numeric anomaly detection uses running mean and standard deviation. It is a
  transparent clue generator, not a fault diagnosis.
- Reservation context must be supplied by an automation or action call.
- The integration does not yet provide a dedicated dashboard card.
- Discovery recommendations are reviewed through a diagnostic sensor and the
  standard integration options rather than a dedicated recommendation card.

## Development

The repository includes pure-Python tests for memory models and baseline logic,
plus HACS and Hassfest validation workflows.

```bash
python -m pytest
python scripts/validate_repo.py
```

## License

MIT
