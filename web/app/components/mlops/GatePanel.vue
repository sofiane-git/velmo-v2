<script setup lang="ts">
const store = useMlopsGateStore()

const suiteLabels: Record<string, string> = {
  memory: 'Mémoire',
  guardrails: 'Garde-fous',
  quality: 'Qualité'
}

async function launch() {
  if (store.isRunning) return
  try {
    await runGate()
  } catch {
    store.fail(`Le gate s'est interrompu de manière inattendue.`)
  }
}
</script>

<template>
  <UCard>
    <template #header>
      <div class="flex items-center justify-between">
        <span class="font-semibold">Gate qualité MLOps</span>
        <UButton
          :loading="store.isRunning"
          :disabled="store.isRunning"
          @click="launch"
        >
          Lancer le gate
        </UButton>
      </div>
    </template>

    <p
      v-if="store.error"
      class="text-sm text-error"
    >
      {{ store.error }}
    </p>

    <ul
      v-if="store.progress.length || store.isRunning"
      class="space-y-1 text-sm"
    >
      <li
        v-for="(step, i) in store.progress"
        :key="i"
        class="flex items-center gap-2"
      >
        <UIcon
          :name="step.passed === step.cases ? 'i-lucide-check' : 'i-lucide-x'"
          :class="step.passed === step.cases ? 'text-success' : 'text-error'"
        />
        Suite {{ suiteLabels[step.suite] }} — {{ step.passed }}/{{ step.cases }} cas — {{ (step.note * 100).toFixed(0) }}%
      </li>
      <li v-if="store.currentSuite">
        <div class="flex items-center gap-2 text-muted italic">
          <UIcon
            name="i-lucide-loader-circle"
            class="animate-spin"
          />
          Suite {{ suiteLabels[store.currentSuite] }} en cours...
        </div>
        <ul class="mt-1 space-y-0.5 pl-6">
          <li
            v-for="c in store.caseResults"
            :key="c.case_id"
            class="flex items-center gap-2 text-xs"
          >
            <UIcon
              :name="c.passed ? 'i-lucide-check' : 'i-lucide-x'"
              :class="c.passed ? 'text-success' : 'text-error'"
            />
            {{ c.case_id }} — {{ (c.score * 100).toFixed(0) }}%
          </li>
          <li
            v-if="store.currentCase"
            class="flex items-center gap-2 text-xs text-muted italic"
          >
            <UIcon
              name="i-lucide-loader-circle"
              class="animate-spin"
            />
            {{ store.currentCase }}...
          </li>
        </ul>
      </li>
    </ul>

    <UCard
      v-if="store.lastResult"
      :ui="{ body: 'space-y-1' }"
      class="mt-3"
    >
      <p class="text-sm">
        Score global :
        <UBadge :color="store.lastResult.gate_passed ? 'success' : 'error'">
          {{ (store.lastResult.note_globale * 100).toFixed(0) }}%
        </UBadge>
        — {{ store.lastResult.gate_passed ? 'GATE PASSÉ' : 'GATE BLOQUÉ' }}
      </p>
      <p class="text-xs text-muted">
        Mémoire {{ (store.lastResult.note_memory * 100).toFixed(0) }}% · Garde-fous
        {{ (store.lastResult.note_guardrails * 100).toFixed(0) }}% · Qualité
        {{ (store.lastResult.note_quality * 100).toFixed(0) }}%
      </p>
      <p class="text-xs text-muted">
        Coût/conversation {{ store.lastResult.cost_per_conv.toFixed(4) }} € · Latence p95
        {{ store.lastResult.latency_p95_ms.toFixed(0) }} ms
      </p>
    </UCard>
  </UCard>
</template>
