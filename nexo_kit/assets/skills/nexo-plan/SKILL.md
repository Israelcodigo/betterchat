---
name: nexo-plan
description: Convierte un cambio de software en tareas Nexo con dependencias y criterios observables, reutilizando el estado y las decisiones existentes.
---

# Planificar trabajo en Nexo

Resultado: un plan ejecutable para el cambio pedido, con el mínimo grafo de tareas útil. Proyecto instalado: {{PROJECT}}.

## Reconstruir el punto de partida

En PowerShell:

~~~powershell
& {{NEXO_COMMAND}} status
& {{NEXO_COMMAND}} task list
& {{NEXO_COMMAND}} memory search 'términos del cambio'
~~~

Sustituye la búsqueda por términos del dominio. Lee instrucciones del proyecto, código afectado, pruebas existentes y, si existen, glosario y decisiones de arquitectura. Las memorias son antecedentes que hay que contrastar con el código actual; no sustituyen el pedido del usuario.

No vuelvas a crear tareas equivalentes ni conviertas un ajuste trivial en un proyecto de investigación. Conserva el alcance y las preferencias ya acordadas. Resuelve detalles reversibles con evidencia y pregunta solo por una ambigüedad que cambie el resultado esperado o por una autorización todavía ausente.

## Construir el contrato

Por tarea, registra en la descripción:

- Resultado observable y motivo.
- Alcance de archivos o módulos y límites.
- Criterios de aceptación que distingan éxito y fracaso.
- Comando o comprobación que produzca evidencia para cada criterio.
- Dependencias reales: qué resultado necesita de otra tarea.
- Incógnitas relevantes y siguiente experimento para resolverlas.

Consulta [contratos y dependencias](references/contracts.md) si hay varios trabajadores o una tarea no se puede verificar todavía.

Crear tareas desde PowerShell:

~~~powershell
& {{NEXO_COMMAND}} task add 'Título concreto' --description 'Resultado; alcance; aceptación; comprobaciones; límites.'
& {{NEXO_COMMAND}} task add 'Tarea dependiente' --description 'Contrato de la segunda tarea.' --depends 'ID_DEVUELTO'
& {{NEXO_COMMAND}} task ready
~~~

Usa los IDs reales devueltos; repite --depends por cada predecesor. Agrupa tareas que modifican conjuntamente la misma interfaz. Separa investigaciones de implementación cuando su resultado pueda cambiar el diseño. Las tareas independientes deben poder verificarse por separado y evitar disputarse los mismos archivos.

## Salida

Presenta el orden de trabajo, qué puede avanzar en paralelo, los checks disponibles y las incertidumbres. Marca los criterios sin prueba como pendientes de definir, sin sustituirlos por “el agente revisará”.

El plan no inicia un runner, publica issues, cambia ramas ni instala dependencias por sí mismo. Continúa con la construcción si ya está solicitada; no añadas una aprobación ceremonial al trabajo autorizado.
