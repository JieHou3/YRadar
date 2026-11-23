# Changelog

## Unreleased

- Search official used inventory in the desktop app and preview filters with fictional listings.
- Receive alerts when a previously observed car first meets your criteria, including after a transport fee change.
- 桌面版支持自定义购车条件和离线预览。已见车源因运费变化首次进入预算时，也会触发提醒。

## 0.1.0 — Initial public package

- Tesla official used Model Y and Model 3 inventory, with deeper Model Y hardware heuristics.
- Model-specific VIN year decoding, including 2025, and explicit evidence sources for hardware.
- White Interior / Long Range AWD / Clean watch profile plus configurable comparison tiers.
- Transport quote/estimate/unknown handling and optional transport-inclusive display and sorting.
- Southern California straight-line distance lookup with OpenStreetMap attribution.
- Complete-scan safeguards, block backoff, and durable SQLite notification retries.
- English application output, English and Chinese guides, architecture diagrams, and synthetic offline demo.
- Offline regression tests, cross-platform CI configuration, and a public-file release audit.

## 中文

首个公开包支持 Tesla 官方 Model Y / Model 3 二手库存；重点提供带依据的配置分析、可配置提醒、运费与距离比较、失败重试，以及双语购车与架构说明。所有演示数据均为虚构。联网抓取仍需在使用者本机验证。
