# WildSeek Factory — constitución (the Brain)

Documento que leen todos los agentes de la fábrica: planner, builder,
approver, healer (muse exec), briefing. Define arquitectura, cómo se
revisa, cómo se escribe código, y qué es criticidad.

## Producto

Inteligencia de riesgo HPAI para fauna silvestre CONUS. La unidad de
producción no es código: es un **run** — un manifest JSON
(`data/runs/nightly_*.json`) + artefactos versionados en `serve_data/`
+ briefing público.

## Arquitectura (invariantes)

```
ENTRADA   APHIS ArcGIS -> data/*.parquet      (fetch_aphis.py)
SPEC      delta + run_spec.json                (agent/planner.py)
BUILDER   kuhpc: risk -> watershed -> exposure -> render -> export
REFIT     (condicional) labels -> dist_frame -> suscept_model -> reservoir
APPROVER  quorum 3/4 revisores                 (agent/qa.py)
¿HUMANO?  refit o gate fallido -> flag_review / PR, no deploy directo
PRODUCCION rsync -> terminal -> /health verify
BRAIN     manifest + shift log (glimmer local)
SELF-HEAL stage falla -> `muse exec` parchea -> 1 retry
```

Invariantes que NINGÚN agente rompe:
- Datos crudos nunca se editan; todo derivado es recomputable
- Grid CONUS 1/100°, EPSG:4326 interno, render final en 3857
- Toda capa servida pasa por `export_serve.py` (único punto de salida)
- `suit_*` se enmascara a la M de ecorregiones del 95% de presencias

## Cómo se revisa (Approver, quorum 3/4)

1. **codigo** — gates duros: conteos mínimos, bounds p_host, PNGs no
   vacíos, geojson parsea
2. **seguridad** — la corrida no colapsa datos vs. run previo
   (detecciones no bajan >5%, cobertura raster estable)
3. **ux** — meta.json completo: todas las llaves que lee el frontend
   (`suit_bbox`, `risk_*`, `reservoir_*`) + archivos existen
4. **brain** — muse-spark lee el manifest+delta y emite veredicto
   razonado (approve/reject + motivo)

≥3/4 -> ship. <3/4 -> bloquea deploy, deja `qa_report.json` + watchlist.

## Criticidad (doble vía)

- **Rutina** (nuevas detecciones, mismas especies): Main directo ->
  producción. No genera estrés.
- **Crítica** (refit del modelo de susceptibilidad, cambio de features,
  código del pipeline): PR / flag humano. La susceptibilidad pública es
  un número científico — cambios llevan manifest + razón legible.

## Cómo se escribe el código

- Compacto, sin nesting innecesario; sigue convenciones del archivo
- Errores en los boundaries correctos (fetch/red: robusto; math interno:
  assert, no try/except por línea)
- Comentarios solo cuando el porqué no es obvio del código
- Nada de secretos en código; claves viven en env/vars

## Auditable · Seguible · Arreglable

- Todo run deja manifest (stage timings, spec, qa, health, shift_log)
- Self-heal registra qué parcheó `muse exec` y si el retry pasó
- Los tifs/parquets intermedios quedan en beegfs — recomputable end-to-end
