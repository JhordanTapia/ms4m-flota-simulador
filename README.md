# IDS26-C8Q1 · Simulador de flota

Aplicación full stack para visualizar la red vial entregada, asignar cinco camiones a pares de carga y descarga conectados, simular su recorrido por la geometría vial y resumir sus velocidades. El frontend React/Leaflet y el backend FastAPI viven en este mismo repositorio y se despliegan juntos en un proyecto de Vercel Services.

## Requisitos

- Python 3.12 o posterior
- Node.js 20 o posterior y npm
- El archivo original `data-prueba.json` en la raíz (incluido; no se modifica)

## Ejecución local

Terminal 1, desde la raíz del repositorio:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --app-dir . --reload --port 8000
```

Terminal 2:

```powershell
cd frontend
npm install
npm run dev
```

Abre <http://localhost:5173>. Vite reenvía `/api` al backend local en `http://127.0.0.1:8000`. Si Windows no reconoce `python`, usa la ruta completa de `python.exe` para crear el entorno. La referencia interactiva de la API está en `/api/docs`; OpenAPI JSON está en `/api/openapi.json`.

Para ejecutar las pruebas de backend: `cd backend` y luego `.venv\Scripts\python.exe -m pytest`.

## API

Todas las respuestas son JSON. Los errores usan `{ "detail": { "error": "...", "message": "..." } }` cuando la petición puede corregirse, y `{ "error": "...", "message": "..." }` para un fallo de datos al arrancar.

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/health` | Estado del servicio, código de examen y resumen de validación |
| `GET` | `/api/network` | Tramos, cargas, descargas y advertencias |
| `GET` | `/api/routes` | Geometrías de los tramos con sus colores |
| `GET` | `/api/locations` | Ubicaciones de carga y descarga |
| `POST` | `/api/simulations` | Crea/reinicia cinco camiones; cuerpo opcional `{"seed": 3811}`. Devuelve la semilla y `started_at` para identificar la ejecución. |
| `GET` | `/api/simulations/current?seed=3811&started_at=...` | Posición, velocidad, estado, recorrido y marcas de tiempo reconstruidos para esa ejecución. Sin parámetros devuelve `sin_iniciar`. |
| `GET` | `/api/simulations/report?seed=3811&started_at=...` | Métricas por camión y explicación heurística de esa ejecución. |
| `GET` | `/api/simulations/snapshot?seed=3811&started_at=...` | Estado y reporte combinados para actualizar la interfaz en una sola solicitud. |

Ejemplo de inicio:

```bash
curl -X POST http://localhost:8000/api/simulations \
  -H 'Content-Type: application/json' -d '{"seed":3811}'
```

Ejemplo de error si no hay par conectado:

```json
{"detail":{"error":"unreachable_network","message":"No hay ningún par de carga y descarga conectado por la red."}}
```

## Diseño y decisiones

**Red y rutas.** Cada par de puntos consecutivos de `Routes[].points` forma una arista no dirigida, con distancia Haversine en metros como costo. Vértices con coordenadas que coinciden a siete decimales se conectan, los nombres se tratan como etiquetas y los identificadores de tramo mantienen el desempate lexicográfico al comparar rutas de igual costo. Las ubicaciones se ajustan al vértice vial más cercano; la API informa cuando ese ajuste excede el radio indicado. Antes de iniciar se filtran solo pares carga/descarga que pertenecen al mismo componente; la respuesta muestra los pares alcanzables y cuántos se descartaron por desconexión. Si no existe ningún par posible, la API responde `422` sin mover un camión. Dijkstra es suficiente para esta red y facilita explicar el costo mínimo. Con más tiempo usaría un índice espacial para el ajuste a geometría, y evaluaría A* con una heurística geográfica admisible para acelerar redes mucho mayores.

**Simulación.** Una semilla configurable (3811 por defecto) elige cinco pares alcanzables de manera reproducible. Los camiones reciben IDs estables, una ruta Dijkstra y velocidades aleatorias uniformes dentro de límites configurables (30–60 km/h por defecto). Cada ejecución queda identificada por su semilla y hora de inicio. El backend reconstruye el avance, las velocidades y las muestras de forma determinista en cada solicitud; no mantiene un proceso ni un estado de simulación en memoria. El navegador conserva la referencia de ejecución en `localStorage`, de modo que la misma simulación continúa después de una recarga y funciona aunque Vercel atienda las solicitudes desde instancias distintas. Las posiciones se interpolan sobre la secuencia vial y se detienen exactamente en el destino.

**Actualizaciones en vivo.** El frontend consulta estado y reporte con polling cada dos segundos. Se eligió por la escala pequeña y porque coincide con el intervalo de simulación; SSE sería una mejora natural si hicieran falta muchos clientes simultáneos. Como la API recalcula cada respuesta desde una referencia de ejecución, no depende de que una función conserve su proceso entre peticiones.

**Frontend.** React conserva la red vial en memoria en el navegador y consulta solo posiciones y métricas durante el polling, para no transferir de nuevo el JSON completo en cada actualización. React-Leaflet dibuja las polilíneas con los colores recibidos, distingue los dos tipos de ubicación y ajusta el encuadre a la red. La interfaz mantiene estados de carga, error y datos vacíos. Se eligió Leaflet por su integración directa con React; con más tiempo añadiría agrupación de marcadores para redes con más ubicaciones y pruebas visuales en distintos tamaños de pantalla. MapLibre sería una alternativa si el mapa requiriera estilos vectoriales personalizados.

**Reporte.** Los promedios son medias aritméticas de las muestras de velocidad en movimiento, que se generan a intervalos uniformes. La explicación se deriva únicamente de esos valores: promedio de flota, extremos por camión, comparación individual con la media, pocas muestras y proporción que supera 55 km/h. Los umbrales de pocas muestras, porcentaje alto y diferencia significativa se configuran por entorno y se devuelven junto al reporte. No se utiliza un LLM, porque las reglas son suficientes para esta entrega; si las explicaciones necesitaran adaptarse a distintos públicos, un LLM podría redactarlas a partir de estos valores estructurados con una plantilla de respaldo.

## Despliegue

1. Importa el repositorio `JhordanTapia/ms4m-flota-simulador` en Vercel.
2. En la configuración del proyecto, selecciona **Services** como framework. La raíz `vercel.json` define el servicio Vite de `frontend/`, el servicio FastAPI del repositorio y las reglas que envían `/api/*` al backend y el resto al frontend.
3. Despliega el proyecto. Ambos servicios comparten el mismo dominio; no hace falta configurar `VITE_API_URL` ni CORS para las solicitudes entre frontend y API en producción. La URL pública del backend puede entregarse como `https://<dominio-vercel>/api/health`; su documentación está en `/api/docs`.
4. Comprueba `/api/health` en el dominio asignado y luego abre la página principal para iniciar la simulación.

Vercel Services está en beta. El proyecto no usa una base de datos para mantener el estado de simulación: el frontend guarda la semilla y la hora inicial, y la API reconstruye la ejecución de forma determinista.

## Configuración

`SIMULATION_SEED`, `MIN_SPEED_KMH`, `MAX_SPEED_KMH`, `SIMULATION_INTERVAL_SECONDS`, `DATA_FILE`, `CORS_ORIGINS`, `FEW_SAMPLES_THRESHOLD`, `HIGH_SPEED_WARNING_PERCENT` y `REPORT_DIFFERENCE_THRESHOLD_KMH` se leen del entorno en backend. La API valida los valores de configuración al iniciar y responde con HTTP 503 y un mensaje JSON consistente si el archivo o la configuración de red no es válido. El frontend acepta `VITE_API_URL`; vacío usa rutas relativas y el proxy de Vite para desarrollo local y Vercel Services en producción.

Los umbrales heurísticos también se configuran por entorno: `FEW_SAMPLES_THRESHOLD` (5), `HIGH_SPEED_WARNING_PERCENT` (25) y `REPORT_DIFFERENCE_THRESHOLD_KMH` (2). Se devuelven junto al reporte para que la explicación pueda interpretarse.

## Uso de IA

Se utilizó un asistente de IA para apoyar la lectura de requisitos, el diseño inicial, la implementación y la revisión de esta solución. Las decisiones de rutas y métricas están implementadas en código verificable, no delegadas a un LLM durante la ejecución. El reporte se calcula de forma determinista con las muestras registradas; la aplicación no envía ubicaciones ni datos a un servicio de generación de texto. El historial de Git y las pruebas muestran los cambios y las verificaciones realizadas.

## Alcance conocido

- La red incluye sectores sin conexión; se mantienen y se identifican, y nunca se traza una línea recta entre componentes.
- El estado y muestras se reinician al reiniciar el proceso; no se requiere una base de datos para este examen.
- El mapa utiliza mosaicos de OpenStreetMap y necesita conexión a Internet para mostrarlos.
- El JSON original se conserva sin cambios para permitir validar la fuente.
