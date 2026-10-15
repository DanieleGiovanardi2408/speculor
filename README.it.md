# Radar

**Radar è un agente per la ricerca di stage che ogni mattina trova, filtra, ordina e prepara le
candidature, e poi aspetta te.**

[English](README.md)

## Human in the loop

Niente viene inviato o candidato in automatico. Radar raccoglie annunci pubblici, scarta quelli
non adatti, ordina gli altri con un LLM e prepara pacchetti di candidatura e bozze di messaggi.
L'utente li legge, li modifica e li approva nella web app. Le candidature le invia sempre
l'utente; i messaggi LinkedIn approvati partono solo dal computer dell'utente con `radar send`,
uno alla volta, ciascuno dopo una conferma esplicita.

## Avvio rapido

```bash
git clone https://github.com/DanieleGiovanardi2408/speculor.git
cd speculor
make install
make dev
# apri http://localhost:5173
```

`make dev` usa dati demo inventati e un LLM finto offline: nessuna chiave API necessaria.

## Configurazione

Copia `config/*.example.yaml` in `config/*.yaml` e `.env.example` in `.env` (tutti ignorati da
git). Le fonti si attivano o disattivano in *Impostazioni*.

## Comandi principali

| Comando | Cosa fa |
|---|---|
| `make run-pipeline` | Esegue la pipeline giornaliera |
| `make report` | Mostra l'ultimo report |
| `make send` | Invia le bozze LinkedIn approvate, una alla volta, con conferma |
| `make send-dry` | Come sopra ma in prova (`--dry-run`), nessuno stato cambia |

La pipeline gira anche ogni giorno alle 07:00 (Europe/Amsterdam) su GitHub Actions; i dati
stanno sul branch orfano `data`, mai su `main`. Dettagli in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) e [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Limiti ed etica

Solo fonti pubbliche, con rate limit, cache, user agent onesto, robots.txt e back-off. Nessuno
scraping con login, nessun cookie in cloud/CI, nessuna elusione di CAPTCHA o anti-bot. Il robots.txt
di LinkedIn vieta `/jobs-guest/` ai crawler non autorizzati: il connettore LinkedIn lo rispetta e
non scarica nulla (per LinkedIn usa gli alert e-mail o "Aggiungi da URL"). Anche l'API di
SmartRecruiters lo vieta, quindi quelle aziende sono disattivate di default. L'invio con linkedin-cli è locale e sotto la
responsabilità dell'utente.

## Licenza

[MIT](LICENSE) © 2026 Daniele Giovanardi
