<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import InputText from 'primevue/inputtext'

const props = defineProps<{ taskId: number }>()
type Run = { id: string; startedAt: string; finishedAt?: string; status: string }
const runs = ref<Run[]>([])
const selected = ref('')
const details = ref<Record<string, unknown>>({})
const error = ref('')
const categories = [
  { key: 'added', label: '新增' },
  { key: 'modified', label: '修改' },
  { key: 'moved', label: '移动' },
  { key: 'deleted', label: '删除候选' },
  { key: 'quarantined', label: '已隔离' },
] as const
type Category = (typeof categories)[number]['key']
const changeType = ref<Category>('added')
const showChanges = ref(false)
const search = ref('')
const first = ref(0)
const pageSize = ref(20)
let detailRequest = 0
function count(key: Category) {
  const value = details.value[key]
  return Array.isArray(value) ? value.length : 0
}
const changeRows = computed(() => {
  const value = details.value[changeType.value]
  if (!Array.isArray(value)) return []
  return value
    .map((entry: unknown, index: number) => {
      if (typeof entry === 'string') return { id: index, from: '', path: entry }
      const item = entry && typeof entry === 'object' ? (entry as Record<string, unknown>) : {}
      return {
        id: index,
        from: typeof item.from === 'string' ? item.from : '',
        path: typeof item.to === 'string' ? item.to : '',
      }
    })
    .filter((row) =>
      `${row.from}\n${row.path}`
        .toLocaleLowerCase()
        .includes(search.value.trim().toLocaleLowerCase()),
    )
})
const pairedPaths = computed(() => changeType.value === 'modified' || changeType.value === 'moved')
const selectedRun = computed(() => runs.value.find((run) => run.id === selected.value))
watch([changeType, search, selected], () => {
  first.value = 0
})
watch(
  () => changeRows.value.length,
  (length) => {
    if (first.value >= length) first.value = 0
  },
)
function openChanges(key: Category) {
  changeType.value = key
  search.value = ''
  first.value = 0
  showChanges.value = true
}
let timer: ReturnType<typeof setTimeout> | undefined
let stopped = false
const labels: Record<string, string> = {
  running: '执行中',
  reported: '报告已生成',
  completed: '同步完成',
  failed: '执行失败',
  cancelled: '已停止',
  interrupted: '服务重启，执行中断',
  timed_out: '执行超时',
}
async function request(path: string, method = 'GET') {
  const response = await fetch(`/api/crontab/${props.taskId}/mirror${path}`, {
    method,
    headers: window.__tenant ? { tenant: window.__tenant } : {},
  })
  if (!response.ok) throw new Error(`请求失败 (${response.status})`)
  const text = await response.text()
  return text ? JSON.parse(text) : null
}
async function loadDetails(id: string) {
  const version = ++detailRequest
  if (selected.value !== id) details.value = {}
  selected.value = id
  const response = await request(`/runs/${id}`)
  if (!stopped && version === detailRequest && selected.value === id)
    details.value = JSON.parse(response.report)
}
async function refresh() {
  try {
    runs.value = await request('/runs')
    if (!selected.value || !runs.value.some((r) => r.id === selected.value))
      selected.value = runs.value[0]?.id ?? ''
    if (selected.value) await loadDetails(selected.value)
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  }
}
async function choose(id: string) {
  try {
    await loadDetails(id)
  } catch {
    error.value = '无法加载变更清单'
  }
}
async function stop() {
  try {
    await request('/stop', 'POST')
    await refresh()
  } catch {
    error.value = '停止失败'
  }
}
async function poll() {
  await refresh()
  if (!stopped) timer = setTimeout(poll, 5000)
}
onMounted(poll)
onBeforeUnmount(() => {
  stopped = true
  clearTimeout(timer)
})
function downloadReport() {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(details.value, null, 2)], { type: 'application/json' }),
  )
  const link = document.createElement('a')
  link.href = url
  link.download = `mirror-${props.taskId}-${selected.value}.json`
  link.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <section class="space-y-3 text-sm">
    <div class="flex items-center gap-2">
      <strong>镜像执行记录</strong>
      <Button label="刷新" size="small" text @click="refresh" />
      <Button
        v-if="runs.some((r) => r.status === 'running')"
        label="停止本次执行"
        severity="warn"
        size="small"
        @click="stop"
      />
    </div>
    <p v-if="error" class="text-red-500">{{ error }}</p>
    <p v-if="!runs.length">尚无镜像记录。可使用任务的“立即执行”按钮开始。</p>
    <div class="max-h-40 overflow-auto space-y-1">
      <button
        v-for="run in runs"
        :key="run.id"
        class="block text-left hover:underline"
        @click="choose(run.id)"
      >
        {{ new Date(run.startedAt).toLocaleString() }} · {{ labels[run.status] || run.status }}
      </button>
    </div>
    <div v-if="selected" class="space-y-2">
      <p v-if="details.phase">
        当前阶段：{{ details.phase }} · {{ details.completed }} / {{ details.total }}
      </p>
      <p v-if="details.error" class="text-red-500">{{ details.error }}</p>
      <div class="flex flex-wrap gap-3">
        <Button
          v-for="entry in categories"
          :key="entry.key"
          :label="`${entry.label}：${count(entry.key)}`"
          size="small"
          outlined
          @click="openChanges(entry.key)"
        />
      </div>
      <Button label="下载完整变更清单" size="small" @click="downloadReport" />
    </div>
    <Dialog
      v-model:visible="showChanges"
      modal
      header="同步变更记录"
      :style="{ width: '64rem' }"
      :breakpoints="{ '960px': '95vw' }"
    >
      <div class="space-y-4">
        <p v-if="selectedRun" class="text-sm text-slate-500">
          {{ new Date(selectedRun.startedAt).toLocaleString() }} ·
          {{ labels[selectedRun.status] || selectedRun.status }}
        </p>
        <div class="flex flex-wrap gap-2">
          <Button
            v-for="entry in categories"
            :key="entry.key"
            :label="`${entry.label} (${count(entry.key)})`"
            size="small"
            :outlined="changeType !== entry.key"
            :aria-pressed="changeType === entry.key"
            @click="changeType = entry.key"
          />
        </div>
        <InputText
          v-model="search"
          placeholder="搜索文件名或路径"
          aria-label="搜索变更文件"
          class="w-full"
        />
        <p v-if="changeType === 'deleted'" class="text-sm text-slate-500">
          云端已不存在的原路径。是否实际移走本地文件，请查看“已隔离”。
        </p>
        <p v-if="changeType === 'quarantined'" class="text-sm text-slate-500">
          已移入隔离区的旧文件；内容替换时保留的旧版本也会列在这里。
        </p>
        <DataTable
          :value="changeRows"
          data-key="id"
          paginator
          v-model:first="first"
          v-model:rows="pageSize"
          :rows-per-page-options="[20, 50, 100]"
          paginator-template="FirstPageLink PrevPageLink PageLinks NextPageLink LastPageLink RowsPerPageDropdown CurrentPageReport"
          current-page-report-template="{first}–{last} / 共 {totalRecords} 条"
          scrollable
          scroll-height="50vh"
          striped-rows
        >
          <template #empty>{{
            search.trim() ? '没有匹配的文件' : '本次执行没有这类变更'
          }}</template>
          <Column v-if="pairedPaths" field="from" header="原路径"
            ><template #body="{ data }"
              ><span class="break-all whitespace-normal">{{ data.from }}</span></template
            ></Column
          >
          <Column field="path" :header="pairedPaths ? '目标路径' : '文件路径'"
            ><template #body="{ data }"
              ><span class="break-all whitespace-normal">{{ data.path }}</span></template
            ></Column
          >
        </DataTable>
      </div>
      <template #footer
        ><Button label="下载完整变更清单" outlined @click="downloadReport" /><Button
          label="关闭"
          @click="showChanges = false"
      /></template>
    </Dialog>
  </section>
</template>
