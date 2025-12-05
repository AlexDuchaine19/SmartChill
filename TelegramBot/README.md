# SmartChill Telegram Bot (Restored Legacy Architecture)

This service is a **fully restored version** of the original monolithic `TelegramBot_Old.py`, rewritten into a clean modular structure while preserving **100% of the original logic, behavior, message format, and flow**.

Modules are now organized for maintainability, but the bot behaves exactly like the legacy version: same alerts, same registration process, same MAC/username flow, same settings workflow, same config responses, same logging style.

---

## Overview

The Telegram Bot provides:

- User registration via MAC address → username flow  
- Device assignment, renaming, deletion  
- Device information panels  
- Settings editing (boolean + numeric values)  
- Remote device configuration via MQTT (config_ack, config_error, config_data)  
- Alerts pushed from MQTT to Telegram with cooldown  
- Automatic periodic registration into Catalog  
- Clean state management and Telegram callback routing  

All flows match the original bot.

---

## Architecture

```
TelegramBot.py        → Main launcher (exactly like old bot)
modules/
 ├─ bot_handlers.py   → Restored full state machine, menus, commands, callbacks
 ├─ mqtt_client.py    → Restored old MQTT alert/config logic
 ├─ catalog_client.py → User/device/catalog operations (unchanged)
 ├─ utils.py          → Validators & helpers
```

The bot uses **telepot** for Telegram communication and **MyMQTT** for MQTT.

---

## MQTT Topics

The bot listens to the same topics as the old system:

- `Group17/SmartChill/<device_id>/alert`
- `Group17/SmartChill/<device_id>/config_ack`
- `Group17/SmartChill/<device_id>/config_error`
- `Group17/SmartChill/<device_id>/config_data`

### MQTT → Telegram Actions

| Topic            | Action |
|------------------|--------|
| `alert`          | Sends user an alert with cooldown + inline menu |
| `config_ack`     | Confirms parameter update |
| `config_error`   | Displays detailed error |
| `config_data`    | Shows parameter value returned by the device |

---

## Telegram Commands

All legacy commands are restored:

- `/start` — Begin registration  
- `/help` — Show help  
- `/mydevices` — Shows and manages user devices  
- `/newdevice` — Add a new device via MAC  
- `/rename` — Rename device  
- `/settings` — Configure device services  
- `/showme` — Show account & device list  
- `/deleteme` — Delete account  
- `/cancel` — Cancel current operation  

Callback buttons provide menus identical to the old inline GUI.

---

## Device Settings

Each device exposes configurable services and parameters.

The bot supports:

- Boolean parameters (ON/OFF)
- Numeric/text parameters (manual entry)
- Device confirmation (config_ack)
- Error display (config_error)

All interactions follow the original multi-step state machine.

---

## Alerts

Alerts forwarded from MQTT include:

- Type  
- Message  
- Severity  
- Associated value  

The bot enforces a **cooldown** to avoid alert spam.  
Inline buttons allow fast navigation to device info or settings.

---

## Catalog Integration

The bot periodically registers itself in the Catalog service exactly as the original did, using a dedicated thread.

The Catalog is used for:

- Linking chat_id ↔ username  
- Assigning devices  
- Looking up device owners  
- Getting services & parameters  
- Renaming and deleting devices  

---

## How to Run

```
python TelegramBot.py
```

Ensure the following components are running:

- Catalog Service  
- MQTT Broker  
- SmartChill devices / simulator  

---

## Summary

This bot is a **faithful restoration** of the legacy SmartChill Telegram bot with:

- Identical behavior  
- Identical flows  
- Identical messages  
- Modern modular structure  

You get the stability and predictability of the old system with the maintainability of a clean architecture.

