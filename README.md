# PS5 for Home Assistant

An unofficial Home Assistant custom integration that uses PlayStation's web API to show your PS5's installed titles, your purchased library and the remote-download queue with progress. You can also start and cancel remote downloads.

> **Disclaimer:** This project is unofficial and is not affiliated with or endorsed by Sony Interactive Entertainment. It uses PlayStation's private web API with the PlayStation app's public client credentials (as the [`psn-api`](https://github.com/achievements-app/psn-api) project does). Remote downloads are write actions on your account. **Use at your own risk.**

## Features

One Home Assistant device is created for the selected PS5.

| Entity | Type | What it shows / does |
|---|---|---|
| Installed titles | sensor | Number of installed titles (apps included). Attribute `titles`: name, title ID, platforms, size, image. |
| Storage used | sensor | Sum of the installed titles' sizes. |
| Download queue | sensor | Number of items in the remote-download queue. Attribute `items` with title, status, percent, bytes, remaining time. |
| Download progress | sensor | Percent of the item that is currently transferring (`unknown` if none). |
| Download time remaining | sensor | Remaining seconds of that item. |
| Downloading | binary sensor | On while the queue is not empty. |
| Download | event | `started`, `completed` or `cancelled`, with `entitlement_id`, `title_id`, `title` and `platform`. Also fires for downloads started on the website or on the console. |
| Game | select | Downloadable titles from your library as `Name (PS5)`. Selecting only remembers the choice. |
| Download selected game | button | Starts the download of the game chosen in the select. |
| Cancel download | button | Cancels the transferring item, otherwise the first queued item. |
| Refresh library | button | Re-reads your purchased library now. |

| Action | What it does |
|---|---|
| `ps5.start_download` | Starts a remote download (`device_id`, `entitlement_id`). |
| `ps5.cancel_download` | Cancels a queued download (`device_id`, `entitlement_id`). |
| `ps5.get_library` | Returns your purchased titles with `name`, `entitlement_id`, `title_id`, `platform`, `image` and `installed`. Optional filters: `platform` (`PS4`/`PS5`) and `installed` (true/false). |

Example automation step:

```yaml
- action: ps5.get_library
  data:
    device_id: 0123456789abcdef0123456789abcdef
    platform: PS5
    installed: false
  response_variable: library
```

### How often it talks to Sony

- Idle: every 10 minutes (2 requests).
- While something is in the queue: every 60 seconds (1 request plus 1 per queued item).
- Library: at startup (cached for 12 hours across restarts), every 12 hours, and on the refresh button.
- A client-side limiter allows at most 150 requests per hour and at least 2 seconds between requests (10 seconds between writes).

## Requirements

- A PS5 in **rest mode** with **Settings → System → Power Saving → Features Available in Rest Mode → Stay Connected to the Internet** enabled. Downloads start and finish in rest mode.
- Home Assistant 2026.9 or newer.

## Installation (HACS)

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/sebastianwilczek/ha-ps5` with type **Integration**.
3. Install **PS5** and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration → PS5**.

## Getting the NPSSO

The integration signs in with an NPSSO, a 64-character token from your PlayStation browser session.

1. Open a **private/incognito** browser window.
2. Sign in at [playstation.com](https://www.playstation.com/).
3. In the same window, open <https://ca.account.sony.com/api/v1/ssocookie>.
4. Copy **everything** shown there, for example `{"npsso":"<64 characters>","expires_in":5183985}`, and paste it into Home Assistant. The 64-character value alone also works, but then Home Assistant cannot warn you before it expires.
5. Close the window **without signing out**. Signing out invalidates the NPSSO.

The NPSSO lasts about **60 days**. Home Assistant shows a repair 7 days before it expires; fixing it opens the re-authentication dialog where you paste a new one. If it expires or is invalidated, Home Assistant asks for a new one through re-authentication.

If you rename the console, use **Reconfigure** on the integration to select it again.

## Limitations

- One PS5 per config entry in this version. Consoles on the same account must have distinct names.
- Sony does not report free space, so there is no free-space sensor.
- It cannot uninstall titles, launch games or wake the console.
- Behavior with the console fully off, with an already installed or already queued title, or with insufficient space is unknown. Sony's error codes are shown as they are.

## Privacy

The NPSSO, refresh token and a random client ID are stored in Home Assistant's config entry storage, like any integration's credentials, and are included in Home Assistant backups. The access token is kept in memory only. Diagnostics redact the NPSSO, tokens, client and console IDs and the account ID. The integration stores no cookies.

## Development

```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest
```

The tests never touch the network: Sony's responses come from recorded fixtures in `tests/fixtures`, and golden tests check that every request matches the verified format byte for byte.

## License

MIT
