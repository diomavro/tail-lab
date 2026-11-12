// The provenance line under a result: what day it is as of, how many bronze
// snapshots it read and the code that computed it. docs/STANDARDS.md: "a
// result without both is not trustworthy and should not be surfaced".

export interface Provenance {
  as_of: string
  snapshot_ids: string[]
  code_sha: string
}

export function provenanceLine(r: Provenance): string {
  const n = r.snapshot_ids.length
  const sha = r.code_sha === 'unknown' ? 'code sha unknown' : r.code_sha.slice(0, 7)
  return `As of ${r.as_of} · ${n} bronze snapshot${n === 1 ? '' : 's'} · ${sha}`
}
