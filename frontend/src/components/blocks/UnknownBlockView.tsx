import type { UnknownBlock } from '../../types'

/**
 * Fallback for any block `type` this frontend doesn't recognize — the
 * required safety net for contract drift across the Django/agent-service
 * <-> frontend boundary. Renders raw JSON rather than crashing the page or
 * silently dropping the block.
 */
export function UnknownBlockView({ block }: { block: UnknownBlock }) {
  return (
    <div className="block card unknown-block">
      <h3>Unrecognized block: {block.type}</h3>
      <pre>{JSON.stringify(block, null, 2)}</pre>
    </div>
  )
}
