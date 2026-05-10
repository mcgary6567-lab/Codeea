package com.codeea.videoeditor.ui.editor

import android.content.Context
import android.net.Uri
import androidx.lifecycle.LiveData
import androidx.lifecycle.MutableLiveData
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.codeea.videoeditor.model.AudioTrack
import com.codeea.videoeditor.model.EditProject
import com.codeea.videoeditor.model.TextOverlay
import com.codeea.videoeditor.model.VideoClip
import com.codeea.videoeditor.model.VideoFilter
import com.codeea.videoeditor.utils.ThumbnailExtractor
import com.codeea.videoeditor.utils.VideoUtils
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.UUID

class EditorViewModel : ViewModel() {

    private val _project = MutableLiveData<EditProject>()
    val project: LiveData<EditProject> = _project

    private val _currentPositionMs = MutableLiveData(0L)
    val currentPositionMs: LiveData<Long> = _currentPositionMs

    private val _isPlaying = MutableLiveData(false)
    val isPlaying: LiveData<Boolean> = _isPlaying

    private val _selectedClipId = MutableLiveData<String?>()
    val selectedClipId: LiveData<String?> = _selectedClipId

    private val _loading = MutableLiveData(false)
    val loading: LiveData<Boolean> = _loading

    private val undoStack = ArrayDeque<EditProject>()
    private val redoStack = ArrayDeque<EditProject>()

    fun initProject(project: EditProject) {
        _project.value = project
    }

    fun addVideoClip(context: Context, uri: Uri) {
        viewModelScope.launch {
            _loading.value = true
            try {
                val durationMs = VideoUtils.getVideoDurationMs(context, uri)
                val name = VideoUtils.getVideoName(context, uri)
                val clip = VideoClip(
                    id = UUID.randomUUID().toString(),
                    uri = uri,
                    name = name,
                    originalDurationMs = durationMs,
                    trimEndMs = durationMs
                )

                val thumbnails = ThumbnailExtractor.extractThumbnails(
                    context, uri,
                    count = 8,
                    widthPx = 112,
                    heightPx = 112
                )
                val updatedClip = clip.copy(thumbnails = thumbnails)

                pushUndoState()
                val current = _project.value ?: return@launch
                current.clips.add(updatedClip)
                _project.postValue(current)
            } finally {
                _loading.postValue(false)
            }
        }
    }

    fun trimSelectedClip(startMs: Long, endMs: Long) {
        val clipId = _selectedClipId.value ?: return
        pushUndoState()
        val project = _project.value ?: return
        val clip = project.clips.find { it.id == clipId } ?: return
        val index = project.clips.indexOf(clip)
        project.clips[index] = clip.copy(trimStartMs = startMs, trimEndMs = endMs)
        _project.value = project
    }

    fun splitClipAtCurrentPosition() {
        val positionMs = _currentPositionMs.value ?: return
        val clipId = _selectedClipId.value ?: return
        val project = _project.value ?: return
        val clip = project.clips.find { it.id == clipId } ?: return
        val index = project.clips.indexOf(clip)

        var timeBeforeClip = 0L
        for (i in 0 until index) timeBeforeClip += project.clips[i].effectiveDurationMs
        val positionInClip = positionMs - timeBeforeClip

        if (positionInClip <= 0 || positionInClip >= clip.effectiveDurationMs) return

        pushUndoState()
        val splitPoint = clip.trimStartMs + (positionInClip * clip.speedMultiplier).toLong()

        val firstClip = clip.copy(id = UUID.randomUUID().toString(), trimEndMs = splitPoint)
        val secondClip = clip.copy(id = UUID.randomUUID().toString(), trimStartMs = splitPoint)

        project.clips.removeAt(index)
        project.clips.add(index, secondClip)
        project.clips.add(index, firstClip)
        _project.value = project
    }

    fun setClipSpeed(clipId: String, speed: Float) {
        pushUndoState()
        val project = _project.value ?: return
        val index = project.clips.indexOfFirst { it.id == clipId }
        if (index < 0) return
        project.clips[index] = project.clips[index].copy(speedMultiplier = speed)
        _project.value = project
    }

    fun setClipVolume(clipId: String, volume: Float) {
        val project = _project.value ?: return
        val index = project.clips.indexOfFirst { it.id == clipId }
        if (index < 0) return
        project.clips[index] = project.clips[index].copy(volume = volume)
        _project.value = project
    }

    fun setClipFilter(clipId: String, filter: VideoFilter, intensity: Float) {
        pushUndoState()
        val project = _project.value ?: return
        val index = project.clips.indexOfFirst { it.id == clipId }
        if (index < 0) return
        project.clips[index] = project.clips[index].copy(filter = filter, filterIntensity = intensity)
        _project.value = project
    }

    fun rotateClip(clipId: String) {
        pushUndoState()
        val project = _project.value ?: return
        val index = project.clips.indexOfFirst { it.id == clipId }
        if (index < 0) return
        val current = project.clips[index]
        project.clips[index] = current.copy(rotation = (current.rotation + 90) % 360)
        _project.value = project
    }

    fun deleteClip(clipId: String) {
        pushUndoState()
        val project = _project.value ?: return
        project.clips.removeAll { it.id == clipId }
        if (_selectedClipId.value == clipId) _selectedClipId.value = null
        _project.value = project
    }

    fun addAudioTrack(context: Context, uri: Uri) {
        viewModelScope.launch {
            val durationMs = withContext(Dispatchers.IO) {
                VideoUtils.getVideoDurationMs(context, uri)
            }
            val name = VideoUtils.getVideoName(context, uri)
            pushUndoState()
            val project = _project.value ?: return@launch
            project.audioTracks.add(
                AudioTrack(
                    id = UUID.randomUUID().toString(),
                    uri = uri,
                    name = name,
                    durationMs = durationMs
                )
            )
            _project.postValue(project)
        }
    }

    fun removeAudioTrack(trackId: String) {
        pushUndoState()
        val project = _project.value ?: return
        project.audioTracks.removeAll { it.id == trackId }
        _project.value = project
    }

    fun addTextOverlay(overlay: TextOverlay) {
        pushUndoState()
        val project = _project.value ?: return
        project.textOverlays.add(overlay)
        _project.value = project
    }

    fun updateTextOverlay(overlay: TextOverlay) {
        val project = _project.value ?: return
        val index = project.textOverlays.indexOfFirst { it.id == overlay.id }
        if (index >= 0) project.textOverlays[index] = overlay
        _project.value = project
    }

    fun removeTextOverlay(overlayId: String) {
        pushUndoState()
        val project = _project.value ?: return
        project.textOverlays.removeAll { it.id == overlayId }
        _project.value = project
    }

    fun setCurrentPosition(positionMs: Long) {
        _currentPositionMs.value = positionMs
    }

    fun setPlaying(playing: Boolean) {
        _isPlaying.value = playing
    }

    fun selectClip(clipId: String?) {
        _selectedClipId.value = clipId
    }

    fun undo() {
        if (undoStack.isEmpty()) return
        val current = _project.value
        if (current != null) redoStack.addLast(current.deepCopy())
        _project.value = undoStack.removeLast()
    }

    fun redo() {
        if (redoStack.isEmpty()) return
        val current = _project.value
        if (current != null) undoStack.addLast(current.deepCopy())
        _project.value = redoStack.removeLast()
    }

    val canUndo: Boolean get() = undoStack.isNotEmpty()
    val canRedo: Boolean get() = redoStack.isNotEmpty()

    private fun pushUndoState() {
        val current = _project.value?.deepCopy() ?: return
        undoStack.addLast(current)
        if (undoStack.size > 30) undoStack.removeFirst()
        redoStack.clear()
    }

    private fun EditProject.deepCopy() = copy(
        clips = clips.toMutableList(),
        audioTracks = audioTracks.toMutableList(),
        textOverlays = textOverlays.toMutableList()
    )
}
