# Headless server

The headless package runs the existing reconstruction tools behind a small,
dependency-free HTTP API and browser interface. It is intended for a trusted
LAN or private overlay network such as Tailscale; it does not provide its own
user accounts.

## Package and processes

`nix build .#server-cuda-sm61` builds a CUDA package specialized for the GTX
1060 (compute capability 6.1). It omits Flutter and exposes one launcher with
two production modes:

```console
photogrammetry-server web --host 0.0.0.0 --port 8080 \
  --state-dir /persist/photogrammetry --web-dir /path/to/server/static

photogrammetry-server worker --state-dir /persist/photogrammetry \
  --tools-dir /path/to/package/usr/bin
```

The web process handles uploads, job state, logs, downloads, and static files.
The worker claims one queued job at a time and starts every native child in its
own process group so cancellation stops the complete pipeline. Splitting the
processes keeps the UI reachable if a GPU tool fails or the worker restarts.

## Jobs and persistence

State is stored in SQLite plus one directory per UUID:

```text
jobs.sqlite3
jobs/<uuid>/
  input/        uploaded JPG/PNG files
  work/         intermediate COLMAP/OpenMVS data
  results/      downloadable mesh, texture, or splat files
  checkpoints/  completed-stage markers used after restart
  job.log       timestamped command output
```

Running work is requeued after a worker restart and completed stages are not
repeated. No job is deleted automatically. New jobs and uploads are rejected
before free space falls below 20 GiB (configurable with
`PHOTOGRAMMETRY_MIN_FREE_BYTES`).

Mesh jobs run image preparation, COLMAP/GLOMAP, OpenMVS densification,
Poisson reconstruction, decimation, and texture generation. Splat jobs share
the COLMAP/GLOMAP stages and then train Brush for 30,000 steps by default.

Completed splat jobs open their highest-numbered PLY checkpoint in an embedded,
interactive viewer with orbit, zoom, touch, and full-screen controls. The viewer
is bundled into the Nix package, so rendering does not depend on a public CDN;
the original checkpoint download links remain available. Rendering is provided
by SuperSplat Viewer 1.30.2 under the MIT license, shipped at `/viewer/LICENSE`.

## HTTP API

- `GET /api/v1/health`
- `POST /api/v1/jobs`
- `PUT /api/v1/jobs/<id>/images/<filename>`
- `POST /api/v1/jobs/<id>/start`
- `GET /api/v1/jobs`
- `GET /api/v1/jobs/<id>`
- `GET /api/v1/jobs/<id>/logs?after=<line>`
- `GET /api/v1/jobs/<id>/artifacts`
- `GET /api/v1/jobs/<id>/artifacts/<name>`
- `POST /api/v1/jobs/<id>/cancel`

Uploads accept JPG, JPEG, and PNG files, require at least three images, limit
each file to 200 MiB, and limit a job to 20 GiB. The browser uploads files
individually so it can show progress without buffering a whole photo set in
memory.

## servOS access

The servOS module exposes port 8080 only through its existing LAN/Tailscale
firewall policy. Use `http://servos:8080` over Tailscale or open the Homepage
tile; when Homepage itself is opened by raw LAN IP, that tile automatically
uses the same IP. Cockpit manages the native web and worker units, and Beszel
reports aggregate host and GPU usage.
