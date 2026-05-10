package com.codeea.videoeditor.ui.export

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.codeea.videoeditor.R
import com.codeea.videoeditor.databinding.ActivityExportBinding
import com.codeea.videoeditor.export.ExportState
import com.codeea.videoeditor.export.VideoExporter
import com.codeea.videoeditor.model.EditProject
import com.codeea.videoeditor.model.ExportConfig
import com.codeea.videoeditor.utils.VideoUtils
import kotlinx.coroutines.flow.launchIn
import kotlinx.coroutines.flow.onEach

class ExportActivity : AppCompatActivity() {

    private lateinit var binding: ActivityExportBinding
    private val exporter by lazy { VideoExporter(this) }
    private var exportConfig = ExportConfig()
    private var isExporting = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityExportBinding.inflate(layoutInflater)
        setContentView(binding.root)

        setupToolbar()
        setupResolutionButtons()
        setupQualityButtons()
        setupFpsButtons()
        setupExportButton()
    }

    private fun setupToolbar() {
        binding.btnBack.setOnClickListener { finish() }
    }

    private fun setupResolutionButtons() {
        val buttons = mapOf(
            binding.btn720p to ExportConfig.Resolution.R720P,
            binding.btn1080p to ExportConfig.Resolution.R1080P,
            binding.btn4K to ExportConfig.Resolution.R4K
        )
        updateResolutionButtons(buttons, ExportConfig.Resolution.R1080P)

        buttons.forEach { (btn, res) ->
            btn.setOnClickListener {
                exportConfig = exportConfig.copy(resolution = res)
                updateResolutionButtons(buttons, res)
                updateEstimatedSize()
            }
        }
    }

    private fun updateResolutionButtons(
        buttons: Map<com.google.android.material.button.MaterialButton, ExportConfig.Resolution>,
        selected: ExportConfig.Resolution
    ) {
        buttons.forEach { (btn, res) ->
            if (res == selected) {
                btn.backgroundTintList = android.content.res.ColorStateList.valueOf(
                    getColor(R.color.primary)
                )
                btn.setTextColor(getColor(R.color.text_on_primary))
            } else {
                btn.backgroundTintList = android.content.res.ColorStateList.valueOf(
                    android.graphics.Color.TRANSPARENT
                )
                btn.setTextColor(getColor(R.color.text_secondary))
            }
        }
    }

    private fun setupQualityButtons() {
        val buttons = mapOf(
            binding.btnQualityLow to ExportConfig.Quality.LOW,
            binding.btnQualityMedium to ExportConfig.Quality.MEDIUM,
            binding.btnQualityHigh to ExportConfig.Quality.HIGH
        )
        updateQualityButtons(buttons, ExportConfig.Quality.MEDIUM)

        buttons.forEach { (btn, quality) ->
            btn.setOnClickListener {
                exportConfig = exportConfig.copy(quality = quality)
                updateQualityButtons(buttons, quality)
                updateEstimatedSize()
            }
        }
    }

    private fun updateQualityButtons(
        buttons: Map<com.google.android.material.button.MaterialButton, ExportConfig.Quality>,
        selected: ExportConfig.Quality
    ) {
        buttons.forEach { (btn, quality) ->
            if (quality == selected) {
                btn.backgroundTintList = android.content.res.ColorStateList.valueOf(
                    getColor(R.color.primary)
                )
                btn.setTextColor(getColor(R.color.text_on_primary))
            } else {
                btn.backgroundTintList = android.content.res.ColorStateList.valueOf(
                    android.graphics.Color.TRANSPARENT
                )
                btn.setTextColor(getColor(R.color.text_secondary))
            }
        }
    }

    private fun setupFpsButtons() {
        val buttons = mapOf(
            binding.btn24fps to 24,
            binding.btn30fps to 30,
            binding.btn60fps to 60
        )
        updateFpsButtons(buttons, 30)

        buttons.forEach { (btn, fps) ->
            btn.setOnClickListener {
                exportConfig = exportConfig.copy(fps = fps)
                updateFpsButtons(buttons, fps)
            }
        }
    }

    private fun updateFpsButtons(
        buttons: Map<com.google.android.material.button.MaterialButton, Int>,
        selected: Int
    ) {
        buttons.forEach { (btn, fps) ->
            if (fps == selected) {
                btn.backgroundTintList = android.content.res.ColorStateList.valueOf(
                    getColor(R.color.primary)
                )
                btn.setTextColor(getColor(R.color.text_on_primary))
            } else {
                btn.backgroundTintList = android.content.res.ColorStateList.valueOf(
                    android.graphics.Color.TRANSPARENT
                )
                btn.setTextColor(getColor(R.color.text_secondary))
            }
        }
    }

    private fun updateEstimatedSize() {
        val project = intent.getSerializableExtra(EXTRA_PROJECT) as? EditProject ?: return
        val durationSeconds = project.totalDurationMs / 1000
        val sizeMb = exportConfig.estimatedSizeMb(durationSeconds)
        binding.tvEstimatedSize.text = "Estimated file size: ~$sizeMb MB"
    }

    private fun setupExportButton() {
        binding.btnStartExport.setOnClickListener {
            if (!isExporting) startExport()
        }
    }

    private fun startExport() {
        val project = intent.getSerializableExtra(EXTRA_PROJECT) as? EditProject ?: return

        if (project.clips.isEmpty()) {
            Toast.makeText(this, "No video clips to export", Toast.LENGTH_SHORT).show()
            return
        }

        isExporting = true
        binding.btnStartExport.isEnabled = false
        binding.btnStartExport.text = "Exporting..."
        binding.layoutProgress.visibility = View.VISIBLE

        val outputFile = VideoUtils.createOutputFile(this)

        exporter.export(project, outputFile, exportConfig)
            .onEach { state ->
                when (state) {
                    is ExportState.Progress -> {
                        binding.progressExport.progress = state.percent
                        binding.tvExportStatus.text = "Exporting... ${state.percent}%"
                    }
                    is ExportState.Success -> {
                        isExporting = false
                        binding.layoutProgress.visibility = View.GONE
                        binding.btnStartExport.text = "Export Again"
                        binding.btnStartExport.isEnabled = true

                        VideoUtils.saveVideoToGallery(this, state.outputFile)
                        Toast.makeText(this, "Video exported successfully!", Toast.LENGTH_LONG).show()

                        showShareOptions(state.outputFile.absolutePath)
                    }
                    is ExportState.Failure -> {
                        isExporting = false
                        binding.layoutProgress.visibility = View.GONE
                        binding.btnStartExport.text = "Export Video"
                        binding.btnStartExport.isEnabled = true
                        Toast.makeText(this, "Export failed: ${state.error}", Toast.LENGTH_LONG).show()
                    }
                }
            }
            .launchIn(lifecycleScope)
    }

    private fun showShareOptions(filePath: String) {
        val file = java.io.File(filePath)
        val uri = androidx.core.content.FileProvider.getUriForFile(
            this, "${packageName}.fileprovider", file
        )
        val shareIntent = Intent(Intent.ACTION_SEND).apply {
            type = "video/mp4"
            putExtra(Intent.EXTRA_STREAM, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        startActivity(Intent.createChooser(shareIntent, "Share Video"))
    }

    companion object {
        const val EXTRA_PROJECT = "extra_project"

        fun start(context: Context, project: EditProject) {
            val intent = Intent(context, ExportActivity::class.java).apply {
                putExtra(EXTRA_PROJECT, project)
            }
            context.startActivity(intent)
        }
    }
}
