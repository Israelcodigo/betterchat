# Descartes y aspectos sin resolver

Las exclusiones mantienen coherente el kit para Claude Code y Codex CLI en Windows. Una función descartada puede ser valiosa en otro contexto; no se presenta como inferior sin medición.

## Descartado deliberadamente

| Pieza o enfoque | Motivo | Alternativa incluida |
|---|---|---|
| Instalar todos los harnesses y sus hooks | Duplicaría estado, instrucciones, captura y bucles de continuación. | Un runtime, una base de estado y adaptadores por host. |
| Catálogos completos de cientos de agentes | Aumentan solapamientos sin demostrar utilidad para el trabajo general. | Cuatro roles y nueve skills con tareas distintas en 1.1. |
| openai/skills y get-shit-done como bases activas | La investigación previa registró deprecación/archivo y sucesores. | Referencias actuales y arquitectura propia. |
| Redistribuir skills de documentos de Anthropic | Tienen condiciones por skill; no se asume licencia uniforme. | Solo la técnica de empaquetado modular. |
| Copiar runtime/prompts de context-mode | ELv2 identificada; no se incluye en la distribución original. | Implementación propia del patrón almacenamiento/búsqueda de salidas. |
| Copiar pro-workflow mientras falta LICENSE | Hay MIT en package.json, pero falta el texto de concesión en la inspección. | Registro original de candidatos; la discrepancia está documentada. |
| Copiar contenido de Trail of Bits o Task Observer | No hace falta redistribuir sus textos para aplicar los principios estudiados. | Guías y código originales con procedencia conceptual. |
| Guardar cada evento, corrección o sesión global | Excede el alcance del proyecto y puede persistir ruido o datos sensibles. | Captura delimitada y memorias con fuente; ingestas explícitas. |
| Instalar reglas sin autorización ni evidencia | Una corrección local puede estar mal interpretada o perder vigencia. | Memorias con aprobación explícita; skills repetidas con preferencia de automejora autorizada, caso nuevo, revisión y validación. |
| Daemon LLM de aprendizaje automático | Añade llamadas, coste y decisiones no visibles en el flujo principal. | Aprendizaje dentro del trabajo mediante nexo-learn; selección automática cuando el usuario lo autoriza. |
| Embeddings, grafo semántico y servidor de memoria | Requieren proveedores, modelos o servicios; beneficio no medido para este kit. | FTS5/BM25 y fallback lexical local. |
| Dolt y sincronización entre máquinas | Amplían considerablemente el contrato de consistencia. | Transacciones SQLite en una máquina. |
| Enjambres, consenso y votación de modelos | Complejidad sin evidencia comparativa de mejor calidad para este uso. | DAG, propiedad de tareas y revisión independiente. |
| Gateway de mensajería, cron y agente Hermes completo | Hermes es otro agente, no un harness drop-in de estos clientes. | Solo técnicas de memoria y recuperación, reimplementadas. |
| Tmux/psmux como base de coordinación | Añaden fricción y diferencias Windows/WSL. | Procesos en primer plano y runtime portable. |
| Copiar perfiles fijos de modelo y esfuerzo | Pueden contradecir preferencias vigentes y cambian con el proveedor. | Preferencias del host y backend elegido por el usuario. |
| Shell parser como sandbox universal | Una lista de patrones no modela todos los efectos de un programa. | Permisos nativos del cliente y límites de coordinación bien descritos. |
| Push, PR, publicación o despliegue automáticos | La construcción local del kit no otorga autorización general para esos efectos. | Integración Git local explícita; acciones externas fuera del runner por defecto. |
| Bucle Stop infinito hasta declarar éxito | Puede repetir errores, gastar recursos o bloquear una salida legítima. | Timeouts, ciclos finitos y estados incompletos explícitos. |
| Preguntar por cada paso rutinario o seam de test | La tarea ya autorizada permite decisiones reversibles ordinarias. | Preguntas solo por decisiones o permisos materialmente ausentes. |
| Promesas de ahorro, velocidad o autonomía de horas | No se reprodujeron benchmarks ni comparación entre modelos. | Resultados propios observables y alcance de prueba explícito. |

## No equivale a lo implementado

- Worktrees separan cambios Git; no aíslan procesos a nivel del sistema operativo.
- Logs y fingerprints permiten relacionar contenido y comprobaciones; no proporcionan una firma externa ni protección contra un actor con escritura total en el proyecto.
- FTS5 recupera coincidencias lexicales; no ofrece comprensión semántica equivalente a un embedding.
- Una opinión del revisor no es un gate automático garantizado.
- Un código de salida cero del cliente no significa que la tarea esté completa.
- Una skill instalada en disco no demuestra que una versión concreta del host la haya descubierto.
- Un template de instrucciones compartido no iguala los eventos, permisos o capacidades de Claude Code y Codex.

## Incertidumbres conservadas

1. **Resultados comparativos upstream.** No se ejecutaron suites o benchmarks de terceros. Las estrellas no demuestran usuarios activos, mantenimiento suficiente o calidad.
2. **Metadatos GitHub.** La cuota API impidió revalidar estrellas exactas y pushed_at de los cinco repos de research-extra. Fechas HEAD verificadas y fechas push aportadas se mantienen distintas.
3. **Inventario previo.** Los repos marcados P conservan el nivel de evidencia de la investigación anterior; no hubo una auditoría exhaustiva renovada de cada archivo.
4. **Carga real de hosts.** La matriz de verificación propia debe distinguir contratos y fixtures de ejecución real autenticada de Claude/Codex. Si no se ejecutó esta última, no se afirma.
5. **Calidad del agente.** Los tests deterministas del kit no prueban que cualquier modelo producirá buen código, atenderá todas las instrucciones o encontrará todos los defectos.
6. **Seguridad de memoria.** Redacción por patrones no garantiza retirar todos los secretos. Los logs de verificación pueden requerir inspección y tratamiento por el proyecto; no se deben introducir credenciales en ellos.
7. **Portabilidad futura.** La documentación de un proveedor puede cambiar; no se promete compatibilidad con todas las versiones ni con plugins instalados que modifiquen eventos.
8. **Alcance deliberado.** No hay sincronización de estado multi-máquina, interfaz de enjambre, consenso, índice semántico, gateway de mensajes o daemon de aprendizaje en este kit.

Los fallos o funciones no probadas deben aparecer como tales en el informe de entrega. Ningún descarte se sustituye por un comando vacío, una promesa de desarrollo futuro o una marca de completado sin evidencia.
