<script setup lang="ts">
defineProps<{
  step: number
  read?: MemoryReadPayload
  write?: MemoryWritePayload
}>()
</script>

<template>
  <UCard>
    <template #header>
      <div class="flex items-center gap-2">
        <UBadge
          variant="subtle"
          color="neutral"
        >
          {{ step }}
        </UBadge>
        <span class="font-semibold">Mémoire</span>
      </div>
      <p class="mt-1 text-xs text-muted">
        {{ read
          ? "Lecture : historique récent + faits/épisodes appris sur cet utilisateur, injectés dans le prompt."
          : "Écriture : extraction des faits et procédures à retenir pour les prochains échanges." }}
      </p>
    </template>

    <div
      v-if="read"
      class="mb-3"
    >
      <p class="text-sm text-muted">
        {{ read.history_turns }} tour(s) d'historique
        <span v-if="read.summary_used">(résumé inclus)</span>
      </p>
      <p
        v-if="read.facts_matched.length === 0"
        class="text-sm text-muted"
      >
        Aucun fait trouvé pour cet utilisateur.
      </p>
      <ul
        v-else
        class="text-sm"
      >
        <li
          v-for="f in read.facts_matched"
          :key="f.key"
        >
          <UBadge variant="subtle">
            {{ f.key }}
          </UBadge>
          = {{ f.value }} (confiance {{ f.confidence }})
        </li>
      </ul>
      <p
        v-if="read.episodic_matched.length"
        class="mt-1 text-sm text-muted"
      >
        {{ read.episodic_matched.length }} souvenir(s) épisodique(s) rappelé(s).
      </p>
    </div>

    <div v-if="write">
      <p
        v-if="write.pending"
        class="text-sm text-muted"
      >
        Extraction en cours en arrière-plan (n'attend pas la réponse) — le
        résultat de ce tour n'apparaît pas encore ici.
      </p>
      <p
        v-else-if="write.facts_written.length === 0 && write.procedures_written.length === 0"
        class="text-sm text-muted"
      >
        Rien classé en mémoire long terme pour ce tour.
      </p>
      <ul
        v-else
        class="text-sm"
      >
        <li
          v-for="f in write.facts_written"
          :key="f.key"
        >
          Écrit : <UBadge variant="subtle">
            {{ f.key }}
          </UBadge> = {{ f.value }}
          (confiance {{ f.confidence }})
        </li>
        <li
          v-for="p in write.procedures_written"
          :key="p.trigger"
        >
          Procédure : {{ p.trigger }} → {{ p.rule }}
        </li>
      </ul>
      <p
        v-if="write.episode_created"
        class="mt-1 text-sm text-muted"
      >
        Épisode créé (litige signalé).
      </p>
    </div>
  </UCard>
</template>
