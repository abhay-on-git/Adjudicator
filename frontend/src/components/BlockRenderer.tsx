import type { Block, InteractiveAction } from '../types'
import { ClauseEvidenceBlockView } from './blocks/ClauseEvidenceBlockView'
import { InteractiveActionsBlockView } from './blocks/InteractiveActionsBlockView'
import { LineItemBreakdownBlockView } from './blocks/LineItemBreakdownBlockView'
import { OutcomeBlockView } from './blocks/OutcomeBlockView'
import { RiskSignalBlockView } from './blocks/RiskSignalBlockView'
import { UnknownBlockView } from './blocks/UnknownBlockView'

// The subset of Block whose `type` is a literal we know about. Block itself
// also includes UnknownBlock (type: string), which — being a wide type —
// would defeat TypeScript's exhaustiveness narrowing if included directly
// in the switch below (UnknownBlock is NOT assignable to any single literal
// case, so Extract correctly drops it here). Splitting it out via
// `isKnownBlock` lets the switch over KnownBlock stay genuinely exhaustive
// (see the `never` check in `default`, per the workspace's
// typescript-exhaustive-switch rule).
type KnownBlock =
  | Extract<Block, { type: 'outcome' }>
  | Extract<Block, { type: 'clause_evidence' }>
  | Extract<Block, { type: 'line_item_breakdown' }>
  | Extract<Block, { type: 'interactive_actions' }>
  | Extract<Block, { type: 'risk_signal' }>

const KNOWN_TYPES = new Set<string>([
  'outcome',
  'clause_evidence',
  'line_item_breakdown',
  'interactive_actions',
  'risk_signal',
])

function isKnownBlock(block: Block): block is KnownBlock {
  return KNOWN_TYPES.has(block.type)
}

/** Idiomatic exhaustiveness helper: only compiles if every KnownBlock case
 * has been handled above; if a new block type is ever added to KnownBlock
 * without a matching case, this line fails to compile. */
function assertNever(x: never): never {
  throw new Error(`Unhandled known block type: ${JSON.stringify(x)}`)
}

interface Props {
  block: Block
  onResume?: (action: InteractiveAction) => void
  resuming?: boolean
}

export function BlockRenderer({ block, onResume, resuming }: Props) {
  if (!isKnownBlock(block)) {
    return <UnknownBlockView block={block} />
  }

  switch (block.type) {
    case 'outcome':
      return <OutcomeBlockView block={block} />
    case 'clause_evidence':
      return <ClauseEvidenceBlockView block={block} />
    case 'line_item_breakdown':
      return <LineItemBreakdownBlockView block={block} />
    case 'interactive_actions':
      return <InteractiveActionsBlockView block={block} onResume={onResume} resuming={resuming} />
    case 'risk_signal':
      return <RiskSignalBlockView block={block} />
    default:
      return assertNever(block)
  }
}
