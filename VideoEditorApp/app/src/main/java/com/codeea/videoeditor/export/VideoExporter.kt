package com.codeea.videoeditor.export

import android.content.Context
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.transformer.Composition
import androidx.media3.transformer.EditedMediaItem
import androidx.media3.transformer.EditedMediaItemSequence
import androidx.media3.transformer.ExportException
import androidx.media3.transformer.ExportResult
import androidx.media3.transformer.ProgressHolder
import androidx.media3.transformer.Transformer
import com.codeea.videoeditor.model.EditProject
import com.codeea.videoeditor.model.ExportConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.launch
import java.io.File

sealed class ExportState {
    data class Progress(val percent: Int) : ExportState()
    data class Success(val outputFile: File) : ExportState()
    data class Failure(val error: String) : ExportState()
}

class VideoExporter(private val context: Context) {

    fun export(
        project: EditProject,
        outputFile: File,
        config: ExportConfig
    ): Flow<ExportState> = callbackFlow {
        if (project.clips.isEmpty()) {
            trySend(ExportState.Failure("No video clips to export"))
            close()
            return@callbackFlow
        }

        val editedMediaItems = project.clips.map { clip ->
            val mediaItem = MediaItem.Builder()
                .setUri(clip.uri)
                .setClippingConfiguration(
                    MediaItem.ClippingConfiguration.Builder()
                        .setStartPositionMs(clip.trimStartMs)
                        .setEndPositionMs(clip.trimEndMs)
                        .build()
                )
                .build()
            EditedMediaItem.Builder(mediaItem).build()
        }

        val sequence = EditedMediaItemSequence(editedMediaItems)
        val composition = Composition.Builder(sequence).build()

        val transformer = Transformer.Builder(context)
            .setVideoMimeType(MimeTypes.VIDEO_H264)
            .setAudioMimeType(MimeTypes.AUDIO_AAC)
            .addListener(object : Transformer.Listener {
                override fun onCompleted(composition: Composition, exportResult: ExportResult) {
                    trySend(ExportState.Progress(100))
                    trySend(ExportState.Success(outputFile))
                    close()
                }

                override fun onError(
                    composition: Composition,
                    exportResult: ExportResult,
                    exportException: ExportException
                ) {
                    trySend(ExportState.Failure(exportException.message ?: "Export failed"))
                    close()
                }
            })
            .build()

        transformer.start(composition, outputFile.absolutePath)

        val progressHolder = ProgressHolder()
        val progressJob = launch(Dispatchers.IO) {
            while (true) {
                val progressState = transformer.getProgress(progressHolder)
                if (progressState == Transformer.PROGRESS_STATE_AVAILABLE) {
                    trySend(ExportState.Progress(progressHolder.progress))
                }
                delay(200)
            }
        }

        awaitClose {
            progressJob.cancel()
            transformer.cancel()
        }
    }
}
