# Prueba reproducible de compatibilidad CatedralX ↔ Agents

**Estado:** CI aprobada en `work` para la combinación de SHAs indicada abajo; evidencia de runtime productivo no establecida.  
**Sin despliegue:** este flujo instala dependencias, ejecuta pruebas aisladas y publica evidencia de test; no despliega ni invoca servicios productivos.

## 1. Fuentes exactas

| Componente | Repositorio | Ref fijada |
|---|---|---|
| CatedralX | `Chalamandra-Magistral-deco/CatedralX` | `e26e4a46ad00bcc59de5347176f7c52948f05391` (rama work al preparar el flujo; snapshot de código base derivado de main `62c0214d63f24cc1f5d3670e703baa70c64f2315`) |
| Agents | `Chalamandra-Magistral-deco/chalamandra-agent-engine` | commit del workflow que ejecuta esta prueba; checkout de la rama `work` |

La referencia de CatedralX se fija por SHA completo, no por nombre de rama, para que una ejecución futura no cambie silenciosamente de código.

## 2. Ejecución automatizada

El workflow `.github/workflows/engine-tests.yml` en `work`:
1. Hace checkout de Agents.
2. Hace checkout del SHA exacto de CatedralX en `CatedralX/`, usando `CATEDRALX_READ_TOKEN` para el repositorio privado.
3. Verifica con `git rev-parse HEAD` que el checkout coincide exactamente con el SHA declarado.
4. Instala dependencias Python 3.12.
5. Ejecuta la suite de Agents, incluidos los tests cross-repo disponibles, con `CATEDRALX_PATH` apuntando al checkout fijado.
6. Publica `agents-catedralx-tests.xml` como artefacto de evidencia, incluso si los tests fallan.
7. Ejecuta lint, mypy, escaneo de secretos e inventario SBOM conforme al workflow existente.

Comando reproducible local (en un entorno que tenga ambos repositorios y dependencias):
```bash
export CATEDRALX_PATH="$PWD/CatedralX"
python -m pytest -q --ignore=CatedralX \
  tests/test_opportunity_e2e.py \
  tests/test_catedralx_flow_integration.py
```

Los tests existentes utilizan datos sintéticos, stores temporales, loopback HTTP, secretos efímeros de prueba y claves Ed25519 de test. No deben conectarse a datos reales, cuentas de mercado, producción o despliegues.

## 3. Cobertura contractual requerida

- Entrada API → validación del contrato → TaskBus → worker.
- Propagación de `trace_id` y continuidad de traza al subproceso CatedralX.
- Snapshot firmado y verificación de procedencia (H-02), cuando el test provee la clave efímera requerida.
- Invocación de `BIN/catedral` y validación de `DecisionResult`.
- Recomputación de `evaluation_hash` con el verificador canónico de CatedralX.
- Límites de análisis: `analysis_only=true`, `orders_created=false`, `orders_executed=false`.
- Publicación de candidato separada de aceptación independiente.
- Dictamen de aceptación/rechazo firmado Ed25519 y verificación de la firma.

## 4. Hallazgo de compatibilidad que requiere prueba explícita

En el código inspeccionado:
- Agents `api/worker_loop.py` exige `evaluation` como objeto y que `evaluation.evaluation_hash` coincida con `DecisionResult.evaluation_hash`; luego llama a `BIN/catedral verify-evaluation-hash`.
- CatedralX `elite/contracts/result_store.py` en el SHA fijado implementa envolturas `store_integrity`, secuencia/hash-chain, checkpoint lateral y bloqueo de lectura/escritura. El store rechaza historial legacy no migrado.
- El worker de Agents valida el contrato de salida y el hash de evaluación, pero el fragmento de integración inspeccionado no demuestra que Agents valide directamente los metadatos internos `store_integrity` del ResultStore.

**Interpretación:** la frontera actualmente probada por Agents es el contrato `DecisionResult/evaluation_hash`; la integridad interna del ResultStore es responsabilidad de CatedralX salvo que se acuerde y pruebe un contrato adicional. No declarar compatibilidad de `store_integrity` inter-repositorio sin un test dedicado que lo consuma o verifique explícitamente.

## 5. Criterios de aceptación

La prueba solo se considera aprobada si:
1. El checkout de CatedralX coincide con el SHA completo fijado.
2. Los dos tests cross-repo se ejecutan (no quedan omitidos por falta de checkout).
3. El resultado demuestra continuidad de `trace_id`.
4. El worker acepta solo un resultado CatedralX con `evaluation_hash` recomputable y límites analysis-only intactos.
5. La consulta permanece pendiente hasta la aceptación independiente, y cambia solo con un dictamen válido firmado.
6. Los tests no crean ni ejecutan órdenes.
7. El artefacto JUnit queda adjunto al run y se conserva el SHA de ambos repositorios.

Un test fallido, omitido, o sin artefacto no cuenta como aprobación. Un test sintético aprobado no demuestra runtime productivo.

## 6. Evidencia de CI observada

Ejecución GitHub Actions `45182038530`:
- URL: https://github.com/Chalamandra-Magistral-deco/chalamandra-agent-engine/actions/runs/45182038530
- Agents `work` SHA: `30fb6268151fd0b7395f4371047516596ef8a254`.
- CatedralX checkout verificado: `e26e4a46ad00bcc59de5347176f7c52948f05391`.
- Conclusión: `success`; jobs `unit-tests`, `Scan all Git history with Gitleaks` y `Dependency audit and SBOM` terminaron correctamente.
- En `unit-tests` constan como aprobados checkout de CatedralX, verificación SHA, instalación, pytest, subida del artefacto, Ruff y mypy.
- En el SHA fijado de CatedralX se verificó que existen `BIN/catedral` (blob `47ccaba7…`), `elite/FEX/GOVERNANCE/economic_cycle.py` (`408a8b69…`) y `elite/bin/dispatch_agents.py` (`f371118b…`); por tanto, los guardas de omisión de los dos tests de integración no se activan por ausencia de esos archivos. El workflow exporta `CATEDRALX_PATH` al checkout fijado.
- GitHub registra `agents-catedralx-test-evidence` (8,269 bytes, no expirado) y `sbom-cyclonedx` (2,702 bytes, no expirado).

**Límite de lectura de evidencia:** la API de GitHub confirma la existencia del artefacto, y el workflow declara la ruta `agents-catedralx-tests.xml`; en esta revisión no se extrajo/leyó el XML contenido en el ZIP. Por tanto, no se publica un conteo de tests ni se afirma el contenido interno del ZIP. La conclusión CI success acredita que el comando `pytest` terminó con código cero en ese commit, pero no sustituye el examen del XML ni una ejecución E2E productiva.

La inspección visual de TinyFish detectó la misma ejecución como exitosa y reportó los tres jobs sin fallos. Su lectura de la lista de artefactos fue inconsistente respecto al XML; para la existencia y tamaño de los artefactos prevalece el endpoint de artefactos de GitHub.

## 7. Bloqueos y límites conocidos

- El workflow necesita el secreto de solo lectura `CATEDRALX_READ_TOKEN`; no se inspecciona ni modifica el secreto. Si falta o no tiene acceso, el checkout falla y el bloqueo se reporta.
- La ejecución CI puede no estar disponible desde esta integración; no se simula su resultado.
- No se realiza migración de ResultStore legacy.
- No se cambia código funcional de CatedralX ni Agents.
- No se fusiona `work` a `main`, no se crean ramas auxiliares y no se despliega.

## 8. Base de diseño y referencias

- W3C Trace Context: estándar de propagación interoperable de `traceparent`/`tracestate`, https://www.w3.org/TR/trace-context/
- Pytest `tmp_path`: aislamiento de archivos temporales por prueba, https://docs.pytest.org/en/stable/how-to/tmp_path.html
- Python Cryptography Ed25519: verificación de firma y excepción ante firmas inválidas, https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/
- NIST SP 800-228 (actualizado 2026-03-13): riesgos y controles de protección de APIs en pre-runtime y runtime, https://csrc.nist.gov/pubs/sp/800/228/upd1/final
- GitHub Actions: dependencias por SHA y jobs secuenciados, https://docs.github.com/en/actions/concepts/security/workflow-dependency-lockfiles y https://docs.github.com/en/actions/how-tos/writing-workflows/choosing-what-your-workflow-does/using-jobs-in-a-workflow

Estas referencias orientan la reproducibilidad y el diseño; no sustituyen evidencia de ejecución del repositorio.
