---
name: nexo-review
description: Revisa de forma independiente una propuesta Nexo contra su contrato y su diff, priorizando defectos demostrables y separando revisión humana de gates automáticos.
---

# Revisión independiente

Revisa el resultado producido para {{PROJECT}} antes de integrarlo o declararlo completo. Empieza por el contrato, el diff y los checks, sin adoptar la conclusión del implementador.

~~~powershell
& {{NEXO_COMMAND}} task list
& {{NEXO_COMMAND}} memory search 'área revisada'
~~~

## Procedimiento

- Identifica el checkout o worktree de la tarea. Lee el estado actual y la base del diff; verifica que revisas la propuesta correcta.
- Sigue las rutas de entrada y salida afectadas. Prioriza fallos de comportamiento, pérdida de datos, incompatibilidad de contratos y condiciones de concurrencia relevantes al cambio.
- Contrasta cada criterio con el código y con la evidencia disponible. La existencia de un archivo de tests o de un log no equivale a una ejecución satisfactoria.
- Usa conocimientos anteriores solo cuando sus condiciones sigan vigentes. Un riesgo histórico sin manifestación en este diff es contexto, no un nuevo defecto.
- Si puedes ejecutar una reproducción pequeña sin alterar el trabajo ajeno, hazlo. Separa lo observado de una inferencia que aún necesite comprobarse.

Cada hallazgo debe incluir consecuencia, condición que lo dispara, archivo/línea actual y forma de verificar la corrección. Informa solo problemas accionables; evita imponer preferencias estilísticas que no afecten mantenibilidad o comportamiento.

## Resultado

Devuelve:

1. Hallazgos por impacto, con evidencia.
2. Criterios que no pudiste comprobar.
3. Comprobaciones realizadas y resultado.
4. Recomendación de continuar, corregir o investigar.

No cambies archivos durante una revisión solicitada como solo lectura. Si también se autorizó corregir, haz cambios acotados y vuelve a verificar las rutas afectadas.

Nexo no transforma una opinión del modelo en un gate determinista. No inventes un comando “approve review” ni marques done editando el estado. La finalización requiere los checks configurados sobre el contenido final. Cuando el proyecto necesite una aprobación humana específica, respeta ese requisito existente y deja claro qué acción depende de ella.
