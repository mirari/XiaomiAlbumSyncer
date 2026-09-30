<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'

const props = defineProps<{ taskId: number; run: string; category: string; index: number }>()
const cell = ref<HTMLElement>()
const image = ref('')
const expanded = ref(false)
const message = ref('等待加载')
const copied = ref('')
const file = ref<{ exists: boolean; path: string; folder: string; location: string }>()
let observer: IntersectionObserver | undefined
let disposed = false
const controller = new AbortController()
async function load() {
  observer?.disconnect()
  const base = `/api/crontab/${props.taskId}/mirror/runs/${props.run}/files/${props.category}/${props.index}`
  const options: RequestInit = {
    headers: window.__tenant ? { tenant: window.__tenant } : {},
    signal: controller.signal,
  }
  try {
    const metadata = await fetch(base, options)
    if (!metadata.ok) throw new Error('无法定位文件')
    file.value = await metadata.json()
    if (!file.value?.exists) {
      message.value = '文件已不存在'
      return
    }
    const preview = await fetch(`${base}/preview`, options)
    if (!preview.ok) {
      message.value = '此格式暂无预览'
      return
    }
    const blob = await preview.blob()
    if (!disposed) image.value = URL.createObjectURL(blob)
  } catch {
    if (!disposed) message.value = '预览不可用'
  }
}
async function copy(value: string, label: string) {
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(value)
    else {
      const input = document.createElement('textarea')
      input.value = value
      input.style.position = 'fixed'
      input.style.opacity = '0'
      document.body.appendChild(input)
      input.select()
      const success = document.execCommand('copy')
      input.remove()
      if (!success) throw new Error('copy')
    }
    copied.value = `${label}已复制`
  } catch {
    copied.value = '复制失败，请重试'
  }
}
onMounted(() => {
  observer = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) void load()
  })
  if (cell.value) observer.observe(cell.value)
})
onBeforeUnmount(() => {
  disposed = true
  controller.abort()
  observer?.disconnect()
  if (image.value) URL.revokeObjectURL(image.value)
})
</script>

<template>
  <div ref="cell" class="min-w-0">
    <button
      v-if="image"
      type="button"
      aria-label="放大预览"
      class="block w-full cursor-zoom-in"
      @click="expanded = true"
    >
      <img
        :src="image"
        alt="文件预览"
        class="aspect-square w-full rounded-md bg-slate-100 object-contain dark:bg-slate-950"
      />
    </button>
    <div
      v-else
      class="flex aspect-square items-center justify-center rounded-md bg-slate-100 text-xs text-slate-500 dark:bg-slate-950"
    >
      {{ message }}
    </div>
    <div class="mt-2 flex items-center justify-between gap-2 text-xs text-slate-500">
      <span>{{ file?.location }}</span>
      <Button
        v-if="file?.folder"
        icon="pi pi-folder"
        aria-label="复制文件夹路径"
        title="复制文件夹路径"
        size="small"
        severity="secondary"
        text
        @click="copy(file.folder, '文件夹路径')"
      />
    </div>
    <p v-if="copied" role="status" class="text-xs">{{ copied }}</p>
    <Dialog
      v-model:visible="expanded"
      modal
      header="文件预览"
      :style="{ width: '48rem' }"
      :breakpoints="{ '800px': '95vw' }"
    >
      <img :src="image" alt="放大的文件预览" class="max-h-[70vh] w-full object-contain" />
      <p class="mt-2 break-all text-xs text-slate-500">{{ file?.location }} · {{ file?.path }}</p>
    </Dialog>
  </div>
</template>
