<script setup lang="ts">
import { watch } from 'vue'
import Select from 'primevue/select'
import ToggleSwitch from 'primevue/toggleswitch'
import type { LocalCronForm } from '@/utils/crontabForm'

const form = defineModel<LocalCronForm>('form', { required: true })
watch(
  () => form.value.config.syncMode,
  (mode) => {
    if (mode === 'MIRROR')
      Object.assign(form.value.config, {
        rewriteExifTime: false,
        rewriteFileSystemTime: false,
        notify: false,
      })
  },
)
</script>

<template>
  <div class="space-y-3">
    <label class="block text-sm">本地保存方式</label>
    <Select
      v-model="form.config.syncMode"
      class="w-full"
      :options="[
        { label: '增量备份（上游模式）', value: 'ADD_ONLY' },
        { label: '单向镜像（云端 → 本地）', value: 'MIRROR' },
      ]"
      optionLabel="label"
      optionValue="value"
    />
    <div v-if="form.config.syncMode === 'MIRROR'" class="space-y-3 rounded border p-3 text-sm">
      <label class="flex items-center gap-2"
        ><ToggleSwitch
          v-model="form.config.mirrorAllAlbums"
        />全部相册，包括相机和未来新增相册</label
      >
      <label class="flex items-center gap-2"
        ><ToggleSwitch v-model="form.config.mirrorReportOnly" />仅生成报告，不下载或清理文件</label
      >
      <p>
        按云端相册名保存，支持普通目录和默认的相册／文件名路径模板。云端移除的文件移入隔离区，不自动永久删除。不改写
        EXIF 或文件时间。
      </p>
      <p>每个账号使用独立目录。首次建立基线后，如需更改范围或目录，请新建任务。</p>
    </div>
  </div>
</template>
