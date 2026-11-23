# Prueba reproducible de compatibilidad CatedralX ↔ Agents

**Estado:** CI aprobada en `work` para la combinación de SHAs indicada abajo; evidencia de runtime productivo no establecida.  
**Sin despliegue:** este flujo instala dependencias, ejecuta pruebas aisladas y publica evidencia de test; no despliega ni invoca servicios productivos.

## 1. Fuentes exactas

| Componente | Repositorio | Ref fijada |
|---|---|---|
| CatedralX | `Chalamandra-Magistral-deco/CatedralX` | `41aaa38b96e7c230aa54921a1ee2c8e9c8494e58` (rama work al preparar el flujo; snapshot de código base derivado de main `62c0214d63f24cc1f5d3670e703baa70c64f2315`) |
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
7. Instala las dependencias de test de CatedralX y ejecuta `CatedralX/tests/test_decision_result.py` por separado, publicando `catedralx-result-store-tests.xml`.
8. Ejecuta lint, mypy, escaneo de secretos e inventario SBOM conforme al workflow existente.

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
- CatedralX checkout verificado: `41aaa38b96e7c230aa54921a1ee2c8e9c8494e58`.
- Conclusión: `success`; jobs `unit-tests`, `Scan all Git history with Gitleaks` y `Dependency audit and SBOM` terminaron correctamente.
- En `unit-tests` constan como aprobados checkout de CatedralX, verificación SHA, instalación, pytest, subida del artefacto, Ruff y mypy.
- En el SHA fijado de CatedralX se verificó que existen `BIN/catedral` (blob `47ccaba7…`), `elite/FEX/GOVERNANCE/economic_cycle.py` (`408a8b69…`) y `elite/bin/dispatch_agents.py` (`f371118b…`); por tanto, los guardas de omisión de los dos tests de integración no se activan por ausencia de esos archivos. El workflow exporta `CATEDRALX_PATH` al checkout fijado.
- GitHub registra `agents-catedralx-test-evidence` (8,269 bytes, no expirado) y `sbom-cyclonedx` (2,702 bytes, no expirado).

**Lectura posterior del artefacto:** se descargó el ZIP `agents-catedralx-test-evidence` (artifact ID `14821438302`, SHA-256 `e68faf7706d9cc72a7c18869408c0bd7d4a0a7d1b05e68c1eb8651f426f95e4c`) y se leyó `agents-catedralx-tests.xml`. El XML reporta `tests=404`, `failures=0`, `errors=0`, `skipped=0`, tiempo `53.932 s`, timestamp `2026-11-23T13:15:26.574765+00:00`; se encontraron 404 elementos testcase distribuidos en 56 clases. Esto corresponde a la suite de Agents ejecutada con `--ignore=CatedralX`, no a los tests propios de CatedralX. La conclusión CI success y el XML no sustituyen una ejecución E2E productiva.

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

## 9. Seguimiento de integridad ResultStore (2026-11-23)

Se descargó y examinó el artefacto JUnit del run `45182038530`: 404 tests de Agents, 0 fallos, 0 errores, 0 omitidos. El XML no incluye la suite propia de CatedralX porque el workflow usa `--ignore=CatedralX`.

La revisión de código detectó una distinción de confianza que debe quedar explícita:
- CatedralX `elite/contracts/result_store.py` mantiene un hash-chain y un archivo lateral `.head`.
- El propio código declara que el sidecar no es un ancla criptográfica externa: quien pueda reescribir ambos archivos puede recalcularlos.
- Por tanto, el sidecar detecta truncamiento/escritura incompleta respecto al estado local conservado, pero no constituye por sí solo una prueba contra un actor que controle ambos archivos.
- La implementación de Agents inspeccionada valida `DecisionResult/evaluation_hash` mediante el CLI canónico; no se ha demostrado que lea/verifique los metadatos internos `store_integrity`.

Cambios nuevos en CatedralX `work`:
- `elite/contracts/result_store.py`: commit `411469e4af7b1f813765f7e97d3bd5edebfa6fb7`; se corrigió la descripción del checkpoint para llamarlo lateral/local, evitando atribuirle confianza externa.
- `tests/test_decision_result.py`: commit `2830502323d6f12610aa8ec6abef9cd470ce6d68`; añade pruebas negativas de modificación de un registro y reescritura del checkpoint. La suite ya tenía pruebas de truncamiento de sufijo, historial legacy y duplicación de IDs.

**Estado de estos cambios:** commits publicados en `work`; la ejecución CI posterior a estos commits todavía no está acreditada en este documento. No declarar las pruebas nuevas aprobadas hasta leer el run correspondiente. No se modificó `main`, no se fusionó ni desplegó.

**Diseño pendiente:** si se requiere resistencia a un actor con control del almacenamiento local, anclar periódicamente `(sequence, head_hash)` mediante firma Ed25519 y persistirlo fuera del límite de confianza del host/volumen. La aceptación del ancla debe ser fail-closed; un almacén externo vacío o no disponible debe producir estado `cannot_verify`, no éxito.

## 10. Ejecución de pruebas propias de CatedralX en CI

El workflow de Agents `work` se actualizó en el commit `1b72adad2a480d9d3a1aa1062538b49180a5ddc4` para fijar CatedralX en `41aaa38b96e7c230aa54921a1ee2c8e9c8494e58` e incluir una ejecución independiente de `CatedralX/tests/test_decision_result.py`, con instalación de `CatedralX[test]` y artefacto JUnit propio. Esto separa el conteo de Agents (404) del conteo de pruebas ResultStore de CatedralX.

**Pendiente de evidencia:** la edición del workflow no acredita por sí sola que el nuevo run haya comenzado o terminado. Se debe comprobar el run generado, revisar ambos XML y verificar que la suite CatedralX no quede omitida. No afirmar aprobación hasta contar con esos resultados.

## 11. Hallazgo de CI y frontera de integridad (2026-11-23)

El primer run que incorporó la suite ResultStore fue `45188737066` (commit Agents `6fccdd83ad1b48fde8b228dd52ccc476ef7fb566`).
- Suite Agents: terminó con éxito.
- Suite CatedralX `tests/test_decision_result.py`: 36 tests, 1 fallo, 0 errores, 0 omitidos.
- Fallo único: `test_result_store_concurrent_appends_keep_all_lines_valid` esperaba `result_id` en la raíz de cada línea JSONL. El nuevo esquema guarda cada resultado dentro de `record` y sus metadatos en `store_integrity`; la aserción del test no se había actualizado al envelope.
- Corrección publicada en CatedralX `work`: commit `7d733bdbd4f1634ae099ab8e962dfce9cac0cf0b`; la aserción concurrente ahora lee `record.result_id`.
- Agents `work` actualizó el pin y disparó run `45189122258` con SHA CatedralX `7d733bdbd4f1634ae099ab8e962dfce9cac0cf0b`. Al momento de esta anotación el run seguía en ejecución; no se atribuye aprobación.

### Contrato `evaluation_hash` vs `store_integrity`

- Agents vuelve a calcular `evaluation_hash` del objeto de evaluación mediante `BIN/catedral verify-evaluation-hash`; esto valida el contenido de evaluación.
- CatedralX ResultStore añade `store_integrity` en el envelope JSONL: `sequence`, `previous_record_hash`, `kind`, `result_id`, `content_hash` y `record_hash`, más checkpoint lateral local `.head`.
- La inspección de `api/worker_loop.py` no encontró una llamada que recupere/verifique el envelope del ResultStore ni compare `content_hash`/`record_hash`. El worker recibe `decision_result` como objeto JSON y verifica `evaluation_hash`; no se ha demostrado que verifique la cadena de persistencia.
- Por tanto, **la integridad de almacenamiento no está integrada todavía en el contrato de Membrana API**. No presentar `evaluation_hash` como sustituto de `store_integrity`.
- Próxima implementación propuesta: exponer en el CLI canónico una verificación por `result_id` que llame a `get_result` (verificación integral del JSONL y checkpoint) y compare el registro persistido con el objeto entregado al worker; Agents invocará ese CLI en subproceso con resultado fail-closed. Pruebas cruzadas deberán cubrir registro ausente, objeto distinto, registro alterado, checkpoint divergente y legacy no migrado.

**Límite:** no se implementó aún esa integración. No se cambió `main`, no se fusionó ni desplegó.
