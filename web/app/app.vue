<script setup>
useHead({
  meta: [
    { name: 'viewport', content: 'width=device-width, initial-scale=1' }
  ],
  link: [
    { rel: 'icon', href: '/favicon.ico' }
  ],
  htmlAttrs: {
    lang: 'fr'
  }
})

useSeoMeta({
  title: 'Velmo — Cheminement pédagogique',
  description: 'Démo interne : suivez en direct le parcours complet d\'une requête dans l\'agent Velmo (garde-fous, mémoire, routage).'
})
</script>

<template>
  <UApp>
    <UHeader>
      <template #left>
        <div class="flex items-center gap-4">
          <NuxtLink
            to="/"
            class="font-semibold text-lg"
          >
            Velmo — Cheminement pédagogique
          </NuxtLink>
          <NuxtLink
            to="/mlops"
            class="text-sm text-muted hover:text-default"
          >
            MLOps
          </NuxtLink>
        </div>
      </template>

      <template #right>
        <UColorModeButton />
      </template>
    </UHeader>

    <UMain>
      <NuxtPage />
    </UMain>
  </UApp>
</template>
