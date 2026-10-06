# Volta Documentation

Welcome to the **Volta** technical and operational documentation. Volta is a local-first controller, web interface, and telemetry engine for Korean MTTL-W01 (TONLY / LG U+ Smart Power Strip) hardware.

---

## Documentation Index

- [Architecture & Protocol (`docs/architecture.md`)](./architecture.md): Dial-in architecture, TCP 10086 protocol framing, telemetry parsing, and state lifecycle.
- [Setup & Provisioning (`docs/setup-and-provisioning.md`)](./setup-and-provisioning.md): Step-by-step pairing instructions using Termux or CLI scripts, Wi-Fi AP negotiation, and claiming.
- [Multi-Tenancy & Security (`docs/multi-tenancy.md`)](./multi-tenancy.md): User accounts, scoped API access, token management, and strip ownership transfers.
- [Analytics & Telemetry (`docs/analytics.md`)](./analytics.md): SQLite timeseries schema, power/voltage/temperature logging, leaderboard computation, and downsampling.
- [Hardware & Protocols Reference (`docs/hardware-reference.md`)](./hardware-reference.md): Circuit boards, RTL8711AF MCU, I2C relay codes, USB rail details, and ESP-01 hardware mod notes.
- [Hardware Mod Guide (ESP-01) (`docs/hardware-mod/README.md`)](./hardware-mod/README.md): Circuit wiring, firmware sketch, and pinout instructions for bypassing a dead MCU with an ESP-01.
- [Alexa Smart Home Skill Guide (`docs/alexa-integration-guide.md`)](./alexa-integration-guide.md): Complete AWS Lambda code, discovery, and voice control setup for Echo and Alexa mobile app.
- [Mobile App Developer Handoff (`docs/app-developer-handoff.md`)](./app-developer-handoff.md): Engineering guide for Google Auth, dual hardware safety guards (Voltage & Temp), quotas, and Alexa linking.
- [Future Improvements Roadmap (`docs/future-plans-auth-licensing.md`)](./future-plans-auth-licensing.md): Google Sign-In, free 1-strip lifetime license, dedicated Admin Users console, and native Alexa Smart Home Skill integration.
