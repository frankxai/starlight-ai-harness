import { Plugin } from "@opencode/plugin"
import { appendFile, mkdir } from "node:fs/promises"

// Starlight receipts plugin (OmS L3, Phase 1 auto-capture).
// One receipt per completed agent-loop model step (session.step.started/.ended),
// joined with the step's model/agent info. Auxiliary calls (title, compaction,
// generate) do not emit step events and are excluded; session.usage.updated totals them.
//
// Dedupe: the plugin host may run several setup instances (app + background
// service); each instance claims the step's message ID in shared plugin storage
// before writing, so a step is recorded once. Downstream may still dedupe by
// messageID as belt-and-braces.
//
// Kill switch: STARLIGHT_RECEIPTS=0. Debug mirror: STARLIGHT_RECEIPT_DEBUG=1
// (append full event shapes to opencode_events_raw.jsonl; keep off in normal use).
// Paths: STARLIGHT_RECEIPTS_PATH / STARLIGHT_RECEIPT_DEBUG_PATH override the
// defaults (used by sanitized template installs; unset = Starlight defaults).

const RECEIPTS_PATH = process.env.STARLIGHT_RECEIPTS_PATH
  || "receipts/opencode.jsonl"
const RAW_PATH = process.env.STARLIGHT_RECEIPT_DEBUG_PATH
  || "receipts/opencode_events_raw.jsonl"

async function safeAppend(path: string, line: string) {
  try {
    await mkdir(path.substring(0, path.lastIndexOf("/")), { recursive: true })
    await appendFile(path, line + "\n", "utf8")
  } catch {
    // never break the harness over telemetry
  }
}

export default Plugin.define({
  id: "starlight-receipts",
  async setup(ctx) {
    if (process.env.STARLIGHT_RECEIPTS === "0") return

    const inflight = new Map<string, InflightStep>()
    const controller = new AbortController()
    const debug = process.env.STARLIGHT_RECEIPT_DEBUG === "1"

    async function claimOnce(messageID: string): Promise<boolean> {
      try {
        const key = `seen:${messageID}`
        const prior = await ctx.storage.get(key)
        if (prior) return false
        await ctx.storage.set(key, true)
        return true
      } catch {
        return true // storage unavailable: write anyway, downstream dedupes by messageID
      }
    }

    void (async () => {
      try {
        for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
          try {
            if (debug) {
              await safeAppend(RAW_PATH, JSON.stringify(event).slice(0, 2600))
            }
            const data = (event as any).data
            if (!data) continue
            if (event.type === "session.step.started") {
              const model = data.model ?? {}
              inflight.set(data.assistantMessageID, {
                providerID: model.providerID ?? null,
                modelID: model.id ?? null,
                variant: model.variant ?? null,
                agent: data.agent ?? null,
                startedMs: data.started ?? Date.now(),
              })
            } else if (event.type === "session.step.ended") {
              const s = inflight.get(data.assistantMessageID)
              inflight.delete(data.assistantMessageID)
              if (data.assistantMessageID && !(await claimOnce(data.assistantMessageID))) continue
              const tokens = data.tokens ?? {}
              await safeAppend(
                RECEIPTS_PATH,
                JSON.stringify({
                  ts: new Date().toISOString(),
                  source: "opencode-plugin",
                  sessionID: data.sessionID ?? null,
                  messageID: data.assistantMessageID ?? null,
                  providerID: s?.providerID ?? null,
                  modelID: s?.modelID ?? null,
                  variant: s?.variant ?? null,
                  agent: s?.agent ?? null,
                  finish: data.finish ?? null,
                  tokens: {
                    input: tokens.input ?? 0,
                    output: tokens.output ?? 0,
                    reasoning: tokens.reasoning ?? 0,
                    cache_read: tokens.cache?.read ?? 0,
                    cache_write: tokens.cache?.write ?? 0,
                  },
                  cost_usd: data.cost ?? 0,
                  latency_ms: s ? Date.now() - s.startedMs : null,
                }),
              )
            }
          } catch {
            // per-event errors are swallowed; telemetry must never throw
          }
        }
      } catch {
        // subscription errors (shutdown) are fatal only to telemetry
      }
    })()

    return () => controller.abort()
  },
})

interface InflightStep {
  providerID: string | null
  modelID: string | null
  variant: string | null
  agent: string | null
  startedMs: number
}
