# Launtel for Home Assistant

A simple Home Assistant integration to view your Launtel internet plan and switch plans from Home Assistant.

This is a community project and not affiliated with Launtel.

## What you get
- Sensor that shows your current plan
- Select entity to change plans
- Safe handling of "Change in progress" (select is temporarily disabled and the integration rechecks automatically)

## Install
You can install with HACS (recommended) or manually.

### HACS (Custom Repository)
1) HACS → Integrations → Menu (⋮) → Custom repositories
2) Repository: `https://github.com/yenchenLiu/home-assistant-launtel`, Category: Integration → Add
3) Find "Launtel" in HACS → Install → Restart Home Assistant

### Manual
1) Copy `custom_components/launtel` into your Home Assistant config: `config/custom_components/launtel`
2) Restart Home Assistant

## Set up
1) Settings → Devices & Services → Add Integration → search "Launtel"
2) Enter your Launtel portal username and password
3) Pick the service you want to manage

Optional: enable debug logs

```yaml
logger:
  logs:
    custom_components.launtel: debug
```

## Privacy
Credentials are stored by Home Assistant. All requests go directly from your Home Assistant to Launtel.

## License
GPL-3.0. See LICENSE.

## Credits
Thanks to the Home Assistant community and Launtel users who tested and provided feedback.
