import { useEffect, useRef } from 'react'
import type { SSEEvent } from '../types'

export function useSSE(jobId: string | null, onEvent: (event: SSEEvent) => void) {
  const onEventRef = useRef(onEvent)
  onEventRef.current = onEvent

  useEffect(() => {
    if (!jobId) return

    const es = new EventSource(`/research/${jobId}/stream`)

    es.onmessage = (e) => {
      try {
        const event: SSEEvent = JSON.parse(e.data)
        onEventRef.current(event)
        if (event.type === 'done' || event.type === 'error') {
          es.close()
        }
      } catch {
        // ignore unparseable frames (heartbeat comments never reach onmessage)
      }
    }

    es.onerror = () => es.close()

    return () => es.close()
  }, [jobId])
}
