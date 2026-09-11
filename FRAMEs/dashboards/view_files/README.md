# View Files Dashboard

Self-contained FastAPI webapp for displaying media files in a browser dashboard.

Location: `FRAMEs/dashboards/view_files`

## Features

- Display media from:
  - local file path
  - raw base64 payload
  - `data:<mime>;base64,...` URL
  - inline text/table content
- Render common file/media types:
  - images
  - audio
  - video
  - PDF
  - CSV/TSV tables
  - Markdown rendered as an HTML page
  - text/JSON/XML-like text
  - fallback embedded viewer for other browser-supported formats
- Runtime control endpoints:
  - payload load/info
  - start/stop/loop
  - clear dashboard
  - set background color
  - zoom in/out/reset

## Install

The top-level project already includes `fastapi` and `uvicorn`. If running standalone:

```bash
pip install -r requirements.txt
```

## Run

From this directory:

```bash
cd FRAMEs/dashboards/view_files
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Then open:

```text
http://127.0.0.1:8000/
```

## API

### `POST /payload`

Load a new media payload. Provide exactly one of `path`, `base64`, `data_url`, or `text`.

#### Local file path

```bash
curl -X POST http://127.0.0.1:8000/payload \
  -H 'Content-Type: application/json' \
  -d '{"path":"/absolute/path/to/image.png","autoplay":true}'
```

#### Base64

```bash
curl -X POST http://127.0.0.1:8000/payload \
  -H 'Content-Type: application/json' \
  -d '{"base64":"...","mime_type":"image/png","filename":"image.png"}'
```

#### Data URL

```bash
curl -X POST http://127.0.0.1:8000/payload \
  -H 'Content-Type: application/json' \
  -d '{"data_url":"data:image/png;base64,...","filename":"image.png"}'
```

#### Markdown / Text / CSV table

```bash
# Markdown rendered as HTML
curl -X POST http://127.0.0.1:8000/payload \
  -H 'Content-Type: application/json' \
  -d '{"text":"# Hello\\n\\nThis is **Markdown**.","mime_type":"text/markdown","filename":"note.md"}'

# CSV table
curl -X POST http://127.0.0.1:8000/payload \
  -H 'Content-Type: application/json' \
  -d '{"text":"name,value\\na,1\\nb,2","mime_type":"text/csv","filename":"table.csv"}'
```

### `GET /payload`

Return current payload/dashboard state.

```bash
curl http://127.0.0.1:8000/payload
```

### `GET /api/state`

Return current dashboard state for the browser UI polling loop.

### Control playback

```bash
curl -X POST http://127.0.0.1:8000/control/start
curl -X POST http://127.0.0.1:8000/control/stop
curl -X POST 'http://127.0.0.1:8000/control/loop?loop=true'
```

Alternative body form:

```bash
curl -X POST http://127.0.0.1:8000/control \
  -H 'Content-Type: application/json' \
  -d '{"command":"loop","loop":true}'
```

### Clear dashboard

```bash
curl -X POST http://127.0.0.1:8000/clear
```

### Set background color

```bash
curl -X POST http://127.0.0.1:8000/background \
  -H 'Content-Type: application/json' \
  -d '{"color":"#002b36"}'
```

### Zoom

```bash
curl -X POST http://127.0.0.1:8000/zoom/in
curl -X POST http://127.0.0.1:8000/zoom/out
curl -X POST http://127.0.0.1:8000/zoom/reset
curl -X POST http://127.0.0.1:8000/zoom \
  -H 'Content-Type: application/json' \
  -d '{"level":1.5}'
curl -X POST http://127.0.0.1:8000/zoom \
  -H 'Content-Type: application/json' \
  -d '{"delta":-0.25}'
```

## Notes

- Files loaded from base64/data URL/text are stored in `uploads/`.
- Browser autoplay policies may block automatic audio/video playback until the page receives user interaction.
- Local file paths are served only by this backend process; use paths that the FastAPI process can read.