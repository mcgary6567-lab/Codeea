package com.codeea.videoeditor.ui.editor

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.appcompat.app.AppCompatActivity
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import com.codeea.videoeditor.R
import com.codeea.videoeditor.databinding.ActivityEditorBinding
import com.codeea.videoeditor.model.EditProject
import com.codeea.videoeditor.model.TextOverlay
import com.codeea.videoeditor.model.VideoClip
import com.codeea.videoeditor.model.VideoFilter
import com.codeea.videoeditor.timeline.FilterAdapter
import com.codeea.videoeditor.timeline.TimelineView
import com.codeea.videoeditor.tools.TextOverlayView
import com.codeea.videoeditor.tools.TrimHandleView
import com.codeea.videoeditor.ui.export.ExportActivity
import com.codeea.videoeditor.utils.VideoUtils
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.slider.Slider
import java.util.UUID

class EditorActivity : AppCompatActivity() {

    private lateinit var binding: ActivityEditorBinding
    private val viewModel: EditorViewModel by viewModels()
    private var player: ExoPlayer? = null
    private val handler = Handler(Looper.getMainLooper())
    private var isSeekingFromTimeline = false

    private val pickAudioLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            result.data?.data?.let { uri ->
                contentResolver.takePersistableUriPermission(
                    uri, Intent.FLAG_GRANT_READ_URI_PERMISSION
                )
                viewModel.addAudioTrack(this, uri)
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityEditorBinding.inflate(layoutInflater)
        setContentView(binding.root)

        setupPlayer()
        setupObservers()
        setupToolbar()
        setupTools()
        setupTimeline()
        setupTextOverlayView()

        val project = intent.getSerializableExtra(EXTRA_PROJECT) as? EditProject
        val videoUri = intent.getParcelableExtra<Uri>(EXTRA_VIDEO_URI)

        if (project != null) {
            viewModel.initProject(project)
            if (videoUri != null) {
                viewModel.addVideoClip(this, videoUri)
            }
        } else {
            val newProject = EditProject(id = UUID.randomUUID().toString(), name = "New Project")
            viewModel.initProject(newProject)
            videoUri?.let { viewModel.addVideoClip(this, it) }
        }
    }

    private fun setupPlayer() {
        player = ExoPlayer.Builder(this).build().also { exoPlayer ->
            binding.playerView.player = exoPlayer
            exoPlayer.addListener(object : Player.Listener {
                override fun onIsPlayingChanged(isPlaying: Boolean) {
                    viewModel.setPlaying(isPlaying)
                    binding.ivPlayPause.setImageResource(
                        if (isPlaying) R.drawable.ic_pause_circle else R.drawable.ic_play_circle
                    )
                    if (isPlaying) startPositionUpdater() else stopPositionUpdater()
                }

                override fun onPlaybackStateChanged(playbackState: Int) {
                    if (playbackState == Player.STATE_ENDED) {
                        exoPlayer.seekTo(0)
                        exoPlayer.pause()
                    }
                }
            })
        }
        binding.playerView.setOnClickListener { togglePlayPause() }
        binding.ivPlayPause.setOnClickListener { togglePlayPause() }
    }

    private fun togglePlayPause() {
        player?.let {
            if (it.isPlaying) it.pause() else it.play()
        }
    }

    private fun setupObservers() {
        viewModel.project.observe(this) { project ->
            updatePlayerWithProject(project)
            updateTimeline(project)
            updateDurationDisplay(project)
        }

        viewModel.currentPositionMs.observe(this) { posMs ->
            if (!isSeekingFromTimeline) {
                binding.tvCurrentTime.text = VideoUtils.formatDuration(posMs)
            }
        }

        viewModel.loading.observe(this) { loading ->
            binding.playerView.visibility = if (loading) View.INVISIBLE else View.VISIBLE
        }

        viewModel.selectedClipId.observe(this) { clipId ->
            binding.timelineView.setData(
                viewModel.project.value?.clips ?: emptyList(),
                viewModel.project.value?.audioTracks ?: emptyList(),
                viewModel.project.value?.totalDurationMs ?: 0L
            )
        }
    }

    private fun setupToolbar() {
        binding.btnBack.setOnClickListener {
            returnProjectResult()
            finish()
        }
        binding.btnUndo.setOnClickListener {
            if (viewModel.canUndo) viewModel.undo()
            else Toast.makeText(this, "Nothing to undo", Toast.LENGTH_SHORT).show()
        }
        binding.btnRedo.setOnClickListener {
            if (viewModel.canRedo) viewModel.redo()
            else Toast.makeText(this, "Nothing to redo", Toast.LENGTH_SHORT).show()
        }
        binding.btnExport.setOnClickListener {
            viewModel.project.value?.let { project ->
                ExportActivity.start(this, project)
            }
        }
    }

    private fun setupTools() {
        binding.toolCut.setOnClickListener { showTrimDialog() }
        binding.toolSplit.setOnClickListener { splitAtCurrentPosition() }
        binding.toolMusic.setOnClickListener { showMusicDialog() }
        binding.toolText.setOnClickListener { showTextDialog() }
        binding.toolFilter.setOnClickListener { showFilterDialog() }
        binding.toolSpeed.setOnClickListener { showSpeedPanel() }
        binding.toolVolume.setOnClickListener { showVolumePanel() }
        binding.toolRotate.setOnClickListener { rotateSelectedClip() }
    }

    private fun setupTimeline() {
        binding.timelineView.listener = object : TimelineView.TimelineListener {
            override fun onSeek(positionMs: Long) {
                isSeekingFromTimeline = true
                player?.seekTo(positionMs)
                viewModel.setCurrentPosition(positionMs)
                binding.tvCurrentTime.text = VideoUtils.formatDuration(positionMs)
                isSeekingFromTimeline = false
            }

            override fun onClipSelected(clipId: String?) {
                viewModel.selectClip(clipId)
            }

            override fun onClipTrimmed(clipId: String, startMs: Long, endMs: Long) {
                viewModel.trimSelectedClip(startMs, endMs)
            }
        }
    }

    private fun setupTextOverlayView() {
        binding.textOverlayView.interactionListener = object : TextOverlayView.OverlayInteractionListener {
            override fun onOverlaySelected(overlay: TextOverlay) {}
            override fun onOverlayMoved(overlay: TextOverlay) {
                viewModel.updateTextOverlay(overlay)
            }
        }
    }

    private fun updatePlayerWithProject(project: EditProject) {
        val clips = project.clips
        if (clips.isEmpty()) {
            player?.clearMediaItems()
            return
        }

        player?.let { exo ->
            val mediaItems = clips.map { clip ->
                MediaItem.Builder()
                    .setUri(clip.uri)
                    .setClippingConfiguration(
                        MediaItem.ClippingConfiguration.Builder()
                            .setStartPositionMs(clip.trimStartMs)
                            .setEndPositionMs(clip.trimEndMs)
                            .build()
                    )
                    .build()
            }
            val wasPlaying = exo.isPlaying
            val position = exo.currentPosition
            exo.setMediaItems(mediaItems)
            exo.prepare()
            if (wasPlaying) {
                exo.seekTo(position)
                exo.play()
            }
        }
    }

    private fun updateTimeline(project: EditProject) {
        binding.timelineView.setData(
            project.clips,
            project.audioTracks,
            project.totalDurationMs
        )
        binding.textOverlayView.setOverlays(project.textOverlays)
    }

    private fun updateDurationDisplay(project: EditProject) {
        binding.tvDuration.text = VideoUtils.formatDuration(project.totalDurationMs)
    }

    private fun showTrimDialog() {
        val clipId = viewModel.selectedClipId.value
        val project = viewModel.project.value ?: return
        val clip = if (clipId != null) project.clips.find { it.id == clipId }
        else project.clips.firstOrNull()
        clip ?: return

        val dialog = BottomSheetDialog(this)
        val view = layoutInflater.inflate(R.layout.dialog_trim, null)
        dialog.setContentView(view)

        val trimHandleView = view.findViewById<TrimHandleView>(R.id.trimHandleView)
        val tvStart = view.findViewById<android.widget.TextView>(R.id.tvTrimStart)
        val tvEnd = view.findViewById<android.widget.TextView>(R.id.tvTrimEnd)
        val tvDuration = view.findViewById<android.widget.TextView>(R.id.tvTrimDuration)

        trimHandleView.durationMs = clip.originalDurationMs
        trimHandleView.startMs = clip.trimStartMs
        trimHandleView.endMs = clip.trimEndMs
        trimHandleView.thumbnails = clip.thumbnails

        fun updateLabels(start: Long, end: Long) {
            tvStart.text = VideoUtils.formatDuration(start)
            tvEnd.text = VideoUtils.formatDuration(end)
            tvDuration.text = VideoUtils.formatDuration(end - start)
        }
        updateLabels(clip.trimStartMs, clip.trimEndMs)

        trimHandleView.listener = object : TrimHandleView.TrimListener {
            override fun onTrimChanged(startMs: Long, endMs: Long) {
                updateLabels(startMs, endMs)
            }
            override fun onTrimCommitted(startMs: Long, endMs: Long) {}
        }

        view.findViewById<View>(R.id.btnTrimApply).setOnClickListener {
            viewModel.trimSelectedClip(trimHandleView.startMs, trimHandleView.endMs)
            dialog.dismiss()
        }
        view.findViewById<View>(R.id.btnTrimCancel).setOnClickListener { dialog.dismiss() }

        dialog.show()
    }

    private fun splitAtCurrentPosition() {
        val clipId = viewModel.selectedClipId.value
        if (clipId == null) {
            Toast.makeText(this, "Select a clip first", Toast.LENGTH_SHORT).show()
            return
        }
        viewModel.splitClipAtCurrentPosition()
    }

    private fun showMusicDialog() {
        val dialog = BottomSheetDialog(this)
        val view = layoutInflater.inflate(R.layout.dialog_music, null)
        dialog.setContentView(view)

        val project = viewModel.project.value
        val hasTrack = project?.audioTracks?.isNotEmpty() == true

        val tvTrackName = view.findViewById<android.widget.TextView>(R.id.tvTrackName)
        val tvArtist = view.findViewById<android.widget.TextView>(R.id.tvTrackArtist)
        val btnRemove = view.findViewById<android.widget.ImageButton>(R.id.btnRemoveTrack)

        if (hasTrack) {
            val track = project!!.audioTracks.first()
            tvTrackName.text = track.name
            tvArtist.text = track.artist
            btnRemove.visibility = View.VISIBLE
            btnRemove.setOnClickListener {
                viewModel.removeAudioTrack(track.id)
                dialog.dismiss()
            }
        }

        val sliderMusic = view.findViewById<Slider>(R.id.sliderMusicVolume)
        val tvMusicVol = view.findViewById<android.widget.TextView>(R.id.tvMusicVolume)
        sliderMusic.addOnChangeListener { _, value, _ ->
            tvMusicVol.text = "${value.toInt()}%"
        }

        val sliderOriginal = view.findViewById<Slider>(R.id.sliderOriginalVolume)
        val tvOrigVol = view.findViewById<android.widget.TextView>(R.id.tvOriginalVolume)
        sliderOriginal.addOnChangeListener { _, value, _ ->
            tvOrigVol.text = "${value.toInt()}%"
            viewModel.project.value?.let { p ->
                p.originalVideoVolume = value / 100f
            }
        }

        view.findViewById<View>(R.id.btnPickMusic).setOnClickListener {
            val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                type = "audio/*"
                addCategory(Intent.CATEGORY_OPENABLE)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
            }
            pickAudioLauncher.launch(intent)
            dialog.dismiss()
        }

        dialog.show()
    }

    private fun showTextDialog() {
        val dialog = BottomSheetDialog(this)
        val view = layoutInflater.inflate(R.layout.dialog_add_text, null)
        dialog.setContentView(view)

        val etInput = view.findViewById<android.widget.EditText>(R.id.etTextInput)
        val sliderSize = view.findViewById<Slider>(R.id.sliderTextSize)
        val tvSizeVal = view.findViewById<android.widget.TextView>(R.id.tvTextSizeValue)
        var selectedColor = android.graphics.Color.WHITE

        sliderSize.addOnChangeListener { _, value, _ ->
            tvSizeVal.text = "${value.toInt()}sp"
        }

        val colorPicker = view.findViewById<android.widget.LinearLayout>(R.id.layoutColorPicker)
        val colors = listOf(
            0xFFFFFFFF.toInt(), 0xFF000000.toInt(), 0xFFFF6B35.toInt(),
            0xFF1DB954.toInt(), 0xFF2196F3.toInt(), 0xFFFF9800.toInt(),
            0xFFE91E63.toInt(), 0xFF9C27B0.toInt(), 0xFFFFEB3B.toInt()
        )
        colors.forEach { color ->
            val colorView = View(this).apply {
                layoutParams = android.widget.LinearLayout.LayoutParams(48, 48).apply {
                    marginEnd = 8
                }
                setBackgroundColor(color)
                setOnClickListener {
                    selectedColor = color
                }
            }
            colorPicker.addView(colorView)
        }

        view.findViewById<View>(R.id.btnTextAdd).setOnClickListener {
            val text = etInput.text.toString().trim()
            if (text.isNotEmpty()) {
                val posMs = viewModel.currentPositionMs.value ?: 0L
                val overlay = TextOverlay(
                    id = UUID.randomUUID().toString(),
                    text = text,
                    textSizeSp = sliderSize.value,
                    textColor = selectedColor,
                    startTimeMs = posMs,
                    endTimeMs = posMs + 3000L
                )
                viewModel.addTextOverlay(overlay)
                dialog.dismiss()
            }
        }

        view.findViewById<View>(R.id.btnTextCancel).setOnClickListener { dialog.dismiss() }
        dialog.show()
    }

    private fun showFilterDialog() {
        val clipId = viewModel.selectedClipId.value
        val project = viewModel.project.value ?: return
        val clip = if (clipId != null) project.clips.find { it.id == clipId }
        else project.clips.firstOrNull()
        clip ?: run {
            Toast.makeText(this, "No clip to apply filter to", Toast.LENGTH_SHORT).show()
            return
        }

        val dialog = BottomSheetDialog(this)
        val view = layoutInflater.inflate(R.layout.dialog_filters, null)
        dialog.setContentView(view)

        val rvFilters = view.findViewById<androidx.recyclerview.widget.RecyclerView>(R.id.rvFilters)
        val sliderIntensity = view.findViewById<Slider>(R.id.sliderIntensity)
        val tvIntensity = view.findViewById<android.widget.TextView>(R.id.tvIntensityValue)
        var currentFilter = clip.filter
        var currentIntensity = clip.filterIntensity

        sliderIntensity.value = currentIntensity * 100
        tvIntensity.text = "${(currentIntensity * 100).toInt()}%"
        sliderIntensity.addOnChangeListener { _, value, _ ->
            currentIntensity = value / 100f
            tvIntensity.text = "${value.toInt()}%"
        }

        val filterAdapter = FilterAdapter { filter ->
            currentFilter = filter
        }
        filterAdapter.setSelectedFilter(clip.filter)
        rvFilters.adapter = filterAdapter
        rvFilters.layoutManager = androidx.recyclerview.widget.LinearLayoutManager(
            this, androidx.recyclerview.widget.LinearLayoutManager.HORIZONTAL, false
        )

        view.findViewById<View>(R.id.btnFilterApply).setOnClickListener {
            viewModel.setClipFilter(clip.id, currentFilter, currentIntensity)
            dialog.dismiss()
        }

        dialog.show()
    }

    private fun showSpeedPanel() {
        val clipId = viewModel.selectedClipId.value ?: run {
            Toast.makeText(this, "Select a clip first", Toast.LENGTH_SHORT).show()
            return
        }
        val project = viewModel.project.value ?: return
        val clip = project.clips.find { it.id == clipId } ?: return

        binding.panelSpeed.visibility = View.VISIBLE
        binding.layoutToolOptions.visibility = View.VISIBLE

        val sliderSpeed = binding.panelSpeed.findViewById<Slider>(R.id.sliderSpeed)
        val tvSpeed = binding.panelSpeed.findViewById<android.widget.TextView>(R.id.tvSpeedValue)
        sliderSpeed.value = clip.speedMultiplier.coerceIn(0.25f, 4.0f)
        tvSpeed.text = "${clip.speedMultiplier}x"

        sliderSpeed.addOnChangeListener { _, value, _ ->
            tvSpeed.text = "${String.format("%.2f", value)}x"
        }

        binding.panelSpeed.findViewById<View>(R.id.btnSpeedClose).setOnClickListener {
            val speed = sliderSpeed.value
            viewModel.setClipSpeed(clipId, speed)
            binding.panelSpeed.visibility = View.GONE
            binding.layoutToolOptions.visibility = View.GONE
        }

        val presets = mapOf(
            R.id.speedPreset025 to 0.25f,
            R.id.speedPreset05 to 0.5f,
            R.id.speedPreset1 to 1.0f,
            R.id.speedPreset2 to 2.0f,
            R.id.speedPreset4 to 4.0f
        )
        presets.forEach { (id, speed) ->
            binding.panelSpeed.findViewById<View>(id).setOnClickListener {
                sliderSpeed.value = speed
                tvSpeed.text = "${speed}x"
            }
        }
    }

    private fun showVolumePanel() {
        val clipId = viewModel.selectedClipId.value ?: run {
            Toast.makeText(this, "Select a clip first", Toast.LENGTH_SHORT).show()
            return
        }
        val project = viewModel.project.value ?: return
        val clip = project.clips.find { it.id == clipId } ?: return

        binding.panelVolume.visibility = View.VISIBLE
        binding.layoutToolOptions.visibility = View.VISIBLE

        val sliderVol = binding.panelVolume.findViewById<Slider>(R.id.sliderClipVolume)
        val tvVol = binding.panelVolume.findViewById<android.widget.TextView>(R.id.tvVolumeValue)
        sliderVol.value = (clip.volume * 100).coerceIn(0f, 200f)
        tvVol.text = "${(clip.volume * 100).toInt()}%"

        sliderVol.addOnChangeListener { _, value, _ ->
            tvVol.text = "${value.toInt()}%"
        }

        binding.panelVolume.findViewById<View>(R.id.btnVolumeClose).setOnClickListener {
            viewModel.setClipVolume(clipId, sliderVol.value / 100f)
            binding.panelVolume.visibility = View.GONE
            binding.layoutToolOptions.visibility = View.GONE
        }
    }

    private fun rotateSelectedClip() {
        val clipId = viewModel.selectedClipId.value ?: run {
            Toast.makeText(this, "Select a clip first", Toast.LENGTH_SHORT).show()
            return
        }
        viewModel.rotateClip(clipId)
    }

    private val positionUpdater = object : Runnable {
        override fun run() {
            player?.let { exo ->
                if (exo.isPlaying) {
                    val pos = exo.currentPosition
                    viewModel.setCurrentPosition(pos)
                    binding.tvCurrentTime.text = VideoUtils.formatDuration(pos)
                    binding.timelineView.setCurrentPosition(pos)
                    binding.textOverlayView.setCurrentPosition(pos)
                }
            }
            handler.postDelayed(this, 100)
        }
    }

    private fun startPositionUpdater() {
        handler.removeCallbacks(positionUpdater)
        handler.post(positionUpdater)
    }

    private fun stopPositionUpdater() {
        handler.removeCallbacks(positionUpdater)
    }

    private fun returnProjectResult() {
        val project = viewModel.project.value ?: return
        val resultIntent = Intent().apply {
            putExtra(EXTRA_PROJECT, project)
        }
        setResult(Activity.RESULT_OK, resultIntent)
    }

    override fun onPause() {
        super.onPause()
        player?.pause()
    }

    override fun onDestroy() {
        super.onDestroy()
        stopPositionUpdater()
        player?.release()
        player = null
    }

    companion object {
        const val EXTRA_PROJECT = "extra_project"
        const val EXTRA_VIDEO_URI = "extra_video_uri"

        fun start(context: Context, project: EditProject, videoUri: Uri) {
            val intent = Intent(context, EditorActivity::class.java).apply {
                putExtra(EXTRA_PROJECT, project)
                putExtra(EXTRA_VIDEO_URI, videoUri)
            }
            context.startActivity(intent)
        }

        fun startWithProject(context: Context, project: EditProject) {
            val intent = Intent(context, EditorActivity::class.java).apply {
                putExtra(EXTRA_PROJECT, project)
            }
            context.startActivity(intent)
        }
    }
}
