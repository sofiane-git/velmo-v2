<script setup lang="ts">
defineProps<{ runs: GateRun[] }>()
const emit = defineEmits<{ select: [runId: string] }>()

const originLabels: Record<string, string> = {
  manual: 'Manuel',
  ci: 'CI',
  nightly: 'Nightly',
  hotfix: 'Hotfix'
}
</script>

<template>
  <UCard>
    <template #header>
      <span class="font-semibold">Historique des runs</span>
    </template>

    <p
      v-if="runs.length === 0"
      class="text-sm text-muted"
    >
      Aucun run encore enregistré.
    </p>

    <div
      v-else
      class="overflow-x-auto"
    >
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left text-muted">
            <th class="p-1">
              Version
            </th>
            <th class="p-1">
              Date
            </th>
            <th class="p-1">
              Score global
            </th>
            <th class="p-1">
              Statut
            </th>
            <th class="p-1">
              Origine
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="run in runs"
            :key="run.id"
            class="cursor-pointer hover:bg-muted/10"
            @click="emit('select', run.id)"
          >
            <td class="p-1">
              {{ run.version_tag }}
            </td>
            <td class="p-1">
              {{ new Date(run.ran_at).toLocaleString('fr-FR') }}
            </td>
            <td class="p-1">
              {{ (run.note_globale * 100).toFixed(0) }}%
            </td>
            <td class="p-1">
              <UBadge :color="run.gate_passed ? 'success' : 'error'">
                {{ run.gate_passed ? 'pass' : 'fail' }}
              </UBadge>
            </td>
            <td class="p-1">
              <UBadge variant="subtle">
                {{ originLabels[run.triggered_by] ?? run.triggered_by }}
              </UBadge>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </UCard>
</template>
