# Cómo demostrar una mejora real

Estado: protocolo propuesto; no hay resultados comparativos publicados.
La demo offline comprueba Nexo. El smoke autenticado comprueba una integración.
Ninguno demuestra superioridad de modelos ni del sistema combinado.

## Pregunta

¿Una ruta con varios proveedores resuelve más tareas correctas que cada proveedor
por separado, a igualdad de presupuesto y acceso a herramientas?

## Experimento reproducible

1. Congelar un conjunto de repositorios y tareas con commits de partida, contratos
   y tests de aceptación externos. Incluir fallos, cambios de API, refactors y
   documentación verificable. Separar ajuste de rutas de evaluación final.
2. Fijar las versiones de clientes, modelos solicitados y observados, permisos,
   herramientas, número de intentos y límites de tiempo. No elegir un proveedor
   según el resultado esperado de una tarea del conjunto de evaluación.
3. Ejecutar cada proveedor individual y la combinación sobre copias idénticas.
   Alternar el orden y repetir cada caso al menos tres veces. Conservar todos los
   fallos, timeouts y denegaciones, no solo los casos favorables.
4. Evaluar desde el coordinador con checks inmutables. Revisar manualmente los
   criterios que los tests no cubren, idealmente sin revelar el proveedor.
5. Publicar filas por caso e intento: backend, modelos, hashes, veredicto, tiempo,
   tokens si se reportan y coste observado o estimado con su fuente. Usar `null`
   cuando el dato no exista. No convertir cuota de suscripción en coste cero.
6. Comparar tasa de éxito, regresiones, coste por tarea correcta y latencia.
   Reportar variabilidad e intervalos; publicar la diferencia frente al mejor
   baseline individual. No afirmar mejora si la evidencia no la distingue.

## Criterio para una afirmación pública

Solo anunciar una mejora en el conjunto, versiones y presupuesto medidos. Indicar
tamaño de muestra, repeticiones y limitaciones. El benchmark debe incluir el coste
de planificación y revisión de la combinación. Un segundo modelo puede aumentar
coste o empeorar resultados; el experimento debe poder detectar ambas cosas.

## Pendientes

Credenciales de proveedores adicionales, conjunto de evaluación representativo,
presupuesto monetario acordado, captura normalizada de tokens/costes y ejecución
comparativa. Los perfiles actuales constituyen la infraestructura de selección;
todavía no hay un selector aprendido de calidad ni una tabla de ganadores.
