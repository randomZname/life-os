# Vault

Това е default-ният Obsidian vault path. Замени го със симлинк към своя
истински Obsidian vault, или го отвори директно в Obsidian.

## Структура

```text
vault/
  inbox/      ← чернови от BogiAgent (никакъв send)
  ...         ← всичко останало е твое
```

## Безопасност

BogiAgent пише **само** в `vault/inbox/`. Никога другаде в vault-а.
Чете отвсякъде, но писане е ограничено.
