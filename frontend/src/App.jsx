import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CircleMarker, MapContainer, Polyline, TileLayer, Tooltip, useMap } from 'react-leaflet'
import { Activity, AlertTriangle, ArrowDownToLine, ArrowUpFromLine, Clock3, Gauge, Map as MapIcon, Play, RotateCcw, Truck } from 'lucide-react'

const API = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '')
const RUN_STORAGE_KEY = 'ids26-c8q1-run'
const fmt = (value, digits = 1) => value == null ? '—' : Number(value).toFixed(digits)

function readSavedRun() {
  try {
    const run = JSON.parse(window.localStorage.getItem(RUN_STORAGE_KEY) || 'null')
    return Number.isInteger(run?.seed) && typeof run?.started_at === 'string' ? run : null
  } catch { return null }
}

function runQuery(run) {
  if (!run) return ''
  const params = new URLSearchParams({ seed: String(run.seed), started_at: run.started_at })
  return `?${params.toString()}`
}

function FitNetwork({ routes }) {
  const map = useMap()
  useEffect(() => {
    const points = routes.flatMap((r) => r.points || [])
    if (points.length) map.fitBounds(points, { padding: [28, 28], maxZoom: 15 })
  }, [map, routes])
  return null
}

function MapView({ network, simulation }) {
  const routes = network?.routes || []
  const bounds = useMemo(() => {
    const points = routes.flatMap((r) => r.points || [])
    return points.length ? points[Math.floor(points.length / 2)] : [-15.17, -75.73]
  }, [routes])
  return <MapContainer center={bounds} zoom={13} className="road-map" preferCanvas>
    <FitNetwork routes={routes} />
    <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
    {routes.map((route) => <Polyline key={route.id_trm_cs} positions={route.points} pathOptions={{ color: route.color || '#64748b', weight: 2.3, opacity: 0.66 }} />)}
    {(network?.loads || []).map((item) => <CircleMarker key={`load-${item.id}`} center={item.coor} radius={5} pathOptions={{ color: '#063c35', weight: 1.5, fillColor: '#26c6a2', fillOpacity: 1 }}><Tooltip>{item.name} · Carga</Tooltip></CircleMarker>)}
    {(network?.dumps || []).map((item) => <CircleMarker key={`dump-${item.id}`} center={item.coor} radius={4.5} pathOptions={{ color: '#512420', weight: 1.5, fillColor: '#ff876f', fillOpacity: 1 }}><Tooltip>{item.name} · Descarga</Tooltip></CircleMarker>)}
    {(simulation?.trucks || []).map((truck, index) => <CircleMarker key={truck.id} center={truck.position} radius={8} pathOptions={{ color: '#fff', weight: 2, fillColor: ['#2962ff', '#8d57ea', '#e89419', '#d33b66', '#079d81'][index], fillOpacity: 1 }}><Tooltip direction="top" offset={[0, -8]} permanent>{truck.id} · {fmt(truck.speed_kmh)} km/h</Tooltip></CircleMarker>)}
  </MapContainer>
}

function App() {
  const [network, setNetwork] = useState(null)
  const [simulation, setSimulation] = useState(null)
  const [runRef, setRunRef] = useState(readSavedRun)
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [updated, setUpdated] = useState(null)
  const networkLoaded = useRef(false)

  const get = useCallback(async (path) => {
    const response = await fetch(`${API}${path}`)
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      throw new Error(body.detail?.message || body.message || `Error HTTP ${response.status}`)
    }
    return response.json()
  }, [])

  const refresh = useCallback(async (includeNetwork = false, activeRun = runRef) => {
    const shouldLoadNetwork = includeNetwork || !networkLoaded.current
    try {
      const query = runQuery(activeRun)
      const snapshot = await get(`/api/simulations/snapshot${query}`)
      if (shouldLoadNetwork) {
        setNetwork(await get('/api/network'))
        networkLoaded.current = true
      }
      setSimulation(snapshot.simulation.status === 'sin_iniciar' ? null : snapshot.simulation)
      setReport(snapshot.report)
      setUpdated(new Date())
      setError('')
    } catch (e) { setError(`No se pudo consultar la API: ${e.message}`) }
    finally { if (shouldLoadNetwork) setLoading(false) }
  }, [get, runRef])

  useEffect(() => { refresh(!networkLoaded.current) }, [refresh])
  useEffect(() => {
    const id = window.setInterval(() => refresh(), 2000)
    return () => window.clearInterval(id)
  }, [refresh])

  const start = async () => {
    setStarting(true)
    try {
      const response = await fetch(`${API}/api/simulations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      const body = await response.json()
      if (!response.ok) throw new Error(body.detail?.message || `Error HTTP ${response.status}`)
      const nextRun = { seed: body.seed, started_at: body.started_at }
      try { window.localStorage.setItem(RUN_STORAGE_KEY, JSON.stringify(nextRun)) } catch { /* State remains usable for this tab. */ }
      setRunRef(nextRun)
      setSimulation(body); setReport(null); setError('')
    } catch (e) { setError(`No se pudo iniciar la simulación: ${e.message}`) }
    finally { setStarting(false) }
  }

  const trucks = simulation?.trucks || []
  const running = simulation?.status === 'en_curso'
  const summary = network?.summary

  return <main className="shell">
    <header className="topbar">
      <a className="brand" href="#top"><span className="brand-mark"><Activity size={20} /></span><span>Road<span className="brand-accent">line</span><small>FLEET CONTROL</small></span></a>
      <div className="top-meta"><span className="exam-tag">IDS26-C8Q1</span><span className={`live-state ${error ? 'state-error' : ''}`}><i />{error ? 'API con problemas' : running ? 'Simulación en vivo' : 'Sistema preparado'}</span></div>
    </header>

    <div className="page" id="top">
      <section className="intro"><div><p className="eyebrow">CENTRO DE OPERACIONES <span> / </span> PERÚ</p><h1>Control de flota</h1><p className="subhead">Red vial, seguimiento en tiempo real e indicadores de velocidad.</p></div>
        <button className="primary-button" onClick={start} disabled={loading || starting || !summary?.load_count}><span className="button-icon">{starting ? <Activity size={17} className="spin" /> : simulation ? <RotateCcw size={17} /> : <Play size={17} fill="currentColor" />}</span>{starting ? 'Iniciando…' : simulation ? 'Reiniciar simulación' : 'Iniciar simulación'}</button>
      </section>

      {error && <div className="notice error-notice"><AlertTriangle size={18} /><span>{error}</span><button onClick={() => refresh(!network)}>Reintentar</button></div>}
      {loading ? <div className="loading-panel"><span className="loader" /><p>Cargando datos de la red…</p></div> : !network ? <div className="empty-panel"><AlertTriangle /><h2>Los datos no están disponibles</h2><p>Revisa que el backend esté activo y que el JSON sea válido.</p></div> : <>
        <section className="metric-row">
          <Metric icon={<MapIcon />} label="Tramos viales" value={summary?.route_count ?? 0} note={`${summary?.component_count ?? 0} sectores conectados`} />
          <Metric icon={<ArrowUpFromLine />} label="Puntos de carga" value={summary?.load_count ?? 0} note="Orígenes disponibles" tone="mint" />
          <Metric icon={<ArrowDownToLine />} label="Puntos de descarga" value={summary?.dump_count ?? 0} note="Destinos disponibles" tone="coral" />
          <Metric icon={<Truck />} label="Camiones" value={`${trucks.length} / 5`} note={running ? 'En movimiento' : simulation ? 'Última ejecución' : 'Aún sin iniciar'} tone="violet" />
        </section>

        <section className="main-grid">
          <article className="panel map-panel"><div className="panel-heading"><div><div className="section-kicker">VISTA EN TIEMPO REAL</div><h2>Mapa de la red</h2></div><span className="map-count">{summary?.route_count} tramos</span></div>
            <div className="map-wrap">{network.routes?.length ? <MapView network={network} simulation={simulation} /> : <div className="map-empty">No hay tramos válidos para mostrar.</div>}</div>
            <div className="map-footer"><div className="legend"><span><i className="legend-dot load-dot" />Carga</span><span><i className="legend-dot dump-dot" />Descarga</span><span><i className="legend-truck" />Camión</span><span><i className="legend-line" />Tramo</span></div><span className="refresh-time"><Clock3 size={13} />{updated ? `Actualizado ${updated.toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}` : 'Esperando datos'}</span></div>
          </article>
          <article className="panel fleet-panel"><div className="panel-heading"><div><div className="section-kicker">SEGUIMIENTO</div><h2>Flota activa</h2></div><span className={`count-pill ${running ? 'count-live' : ''}`}>{trucks.length} unidades</span></div>
            {trucks.length ? <div className="truck-list">{trucks.map((truck, index) => <TruckRow key={truck.id} truck={truck} index={index} />)}</div> : <div className="panel-empty"><div className="empty-icon"><Truck size={20} /></div><h3>Sin camiones en ruta</h3><p>Inicia la simulación para asignar cinco unidades a recorridos conectados.</p></div>}
            {simulation?.route_selection?.unreachable_pairs_skipped > 0 && <div className="route-note"><AlertTriangle size={13} /><span>Se omitieron {simulation.route_selection.unreachable_pairs_skipped} pares desconectados; se asignaron solo recorridos alcanzables.</span></div>}
            {simulation && <div className="seed-note"><span>SEMILLA DE EJECUCIÓN</span><b>{simulation.seed}</b><span className="seed-divider" /><span>RANGOS</span><b>{simulation.speed_limits_kmh?.min}–{simulation.speed_limits_kmh?.max} km/h</b></div>}
          </article>
        </section>

        <section className="panel report-panel"><div className="panel-heading report-heading"><div><div className="section-kicker">ANÁLISIS DE VELOCIDAD</div><h2>Reporte por camión</h2></div><span className="metric-note"><Gauge size={15} /> Media aritmética de muestras en movimiento</span></div>
          {report?.trucks?.length ? <><div className="table-scroll"><table><thead><tr><th>Unidad</th><th>Muestras</th><th>Mínima</th><th>Máxima</th><th>Promedio</th><th>&gt; 55 km/h</th><th>Estado</th></tr></thead><tbody>{report.trucks.map((row, i) => <tr key={row.truck_id}><td><span className={`table-truck-dot dot-${i}`} />{row.truck_id}</td><td>{row.samples}</td><td>{fmt(row.min_speed_kmh)} <small>km/h</small></td><td>{fmt(row.max_speed_kmh)} <small>km/h</small></td><td className="avg-cell">{fmt(row.average_speed_kmh)} <small>km/h</small></td><td>{row.above_55_kmh} <small>({fmt(row.above_55_percent, 0)} %)</small></td><td><span className={`status-chip ${row.status === 'finalizado' ? 'status-done' : 'status-moving'}`}>{row.status === 'finalizado' ? 'Finalizado' : 'En ruta'}</span></td></tr>)}</tbody></table></div><div className="explanation"><span className="explanation-mark"><Activity size={16} /></span><div><strong>Lectura del reporte</strong><p>{report.explanation}</p></div></div></> : <div className="report-empty"><Gauge size={20} /><span>Las métricas aparecerán aquí cuando inicies la simulación.</span></div>}
        </section>

        {!!summary?.warnings?.length && <details className="warnings"><summary><AlertTriangle size={15} />{summary.warnings.length} advertencias de validación</summary><ul>{summary.warnings.slice(0, 8).map((warning, i) => <li key={i}>{warning}</li>)}</ul></details>}
      </>}
      <footer><span>IDS26-C8Q1</span><span>Simulación geográfica sobre red vial</span><span>{updated ? 'Actualización cada 2 segundos' : 'Conectando…'}</span></footer>
    </div>
  </main>
}

function Metric({ icon, label, value, note, tone = 'blue' }) {
  return <article className="metric-card"><span className={`metric-icon ${tone}`}>{icon}</span><div><span className="metric-label">{label}</span><strong className="metric-value">{value}</strong><span className="metric-footnote">{note}</span></div></article>
}

function TruckRow({ truck, index }) {
  const updatedAt = truck.updated_at ? new Date(truck.updated_at).toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—'
  return <div className="truck-row"><span className={`truck-avatar avatar-${index}`}><Truck size={16} /></span><div className="truck-info"><strong>{truck.id}</strong><span>{truck.origin.name} <b>→</b> {truck.destination.name}</span></div><div className="truck-right"><strong>{fmt(truck.speed_kmh)} <small>km/h</small></strong><span className={`truck-status ${truck.status === 'finalizado' ? 'finished' : ''}`} title={`Actualizado: ${truck.updated_at || 'sin dato'}`}><i />{truck.status === 'finalizado' ? 'Finalizado' : 'En ruta'}<small>· {updatedAt}</small></span></div></div>
}

export default App
