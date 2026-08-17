# Evaluación de la salida del módulo de auditoría de pedidos

Bloque de Evaluación y Calidad · TFG Íñigo Daza
Módulo evaluado: auditoría de pedidos (Juan Salas · GraphyCems)

## Qué hace

El evaluador **no toma como referencia lo que dice el módulo evaluado**. Lee los
documentos de origen del pedido, calcula por su cuenta qué discrepancias existen
realmente, y sólo entonces contrasta la salida del módulo contra ese cálculo.

De ahí salen las dos magnitudes del veredicto:

- **Exhaustividad** — de las discrepancias que existían, cuántas encontró el módulo.
- **Precisión** — de las incidencias que emitió, cuántas se sostienen documentalmente.

## Uso

1. **Documentos del pedido.** Se suben los PDF. Cada documento se clasifica por su
   contenido, no por el nombre del fichero: orden de fabricación, pedido de cliente
   o presupuesto. Un PDF sin capa de texto se detecta y se advierte.
2. **Respuesta del módulo.** Se pega la respuesta tal como aparece en la interfaz de
   GraphyCems, con sus encabezados de severidad. El evaluador la interpreta y muestra
   qué ha entendido en una tabla revisable, corregible celda a celda antes de puntuar.
   El botón *Pegar la respuesta del 42805* carga la respuesta real de ese pedido.
3. **Evaluación.** Se emite el contraste independiente, el resultado caso a caso y
   el `EvaluationResult` con valoración y aspectos a mejorar, exportable a Markdown.

## Ficheros

| Fichero | Contenido |
|---|---|
| `app.py` | Interfaz de los tres pasos |
| `evaluador.py` | Motor: extracción, cálculo de discrepancias, contraste, veredicto |
| `requirements.txt` | Dependencias de Python |
| `packages.txt` | `poppler-utils`, necesario para `pdftotext`. Sin él la app no arranca |

## Criterios de diseño

- **La tasa de acierto se calcula sólo sobre los casos verificados.** Contar los
  pendientes como superados inflaría el resultado; contarlos como fallidos penalizaría
  al módulo por datos que no dependen de él. Quedan fuera y se declaran.
- **Cada aspecto a mejorar queda anclado al caso que lo evidencia.** No se emiten
  observaciones generales.
- **Un caso que los documentos no permiten juzgar queda pendiente, no superado.**
  Por ejemplo, la cobertura de campos sólo puede evaluarse si existe una discrepancia
  real en un campo distinto de los ya probados.

## Interpretación de la respuesta

La lectura es determinista: reconoce los encabezados de severidad, identifica el campo
por las expresiones que emplea el módulo, separa los dos valores en conflicto por la
conjunción adversativa y atribuye cada uno a su documento según cuál se mencione en
cada lado. Lo que no consigue interpretar se declara como aviso y queda corregible.

Esto cubre la forma en que el módulo redacta hoy. Para formulaciones arbitrarias, la
función `interpretar()` de `evaluador.py` es el punto donde encaja una llamada a un
modelo de lenguaje: sustituyéndola, el resto del evaluador no cambia, porque lo que
se juzga son las incidencias ya interpretadas y no el texto.

## Limitación vigente

La respuesta del módulo se copia a mano porque sólo está disponible como interfaz
renderizada. Todo lo demás es automático. Cuando el módulo exponga su respuesta
estructurada, ese paso desaparece y la cadena funciona sin intervención.
