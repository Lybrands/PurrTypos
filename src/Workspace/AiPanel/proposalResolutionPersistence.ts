export async function persistBookProposalResolution(dependencies: {
  wait(): Promise<void>
  save(): Promise<{ success: boolean }>
  attempts?: number
}): Promise<boolean> {
  const attempts = Math.max(1, dependencies.attempts ?? 3)
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (attempt > 0) await dependencies.wait()
    try {
      const result = await dependencies.save()
      if (result.success) return true
    } catch {
      // Existing conversation merge is idempotent; retry the same occurrence.
    }
  }
  return false
}
