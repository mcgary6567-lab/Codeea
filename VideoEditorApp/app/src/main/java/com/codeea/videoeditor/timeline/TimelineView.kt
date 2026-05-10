package com.codeea.videoeditor.timeline

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import androidx.core.content.ContextCompat
import com.codeea.videoeditor.R
import com.codeea.videoeditor.model.AudioTrack
import com.codeea.videoeditor.model.VideoClip
import kotlin.math.max
import kotlin.math.min

class TimelineView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    interface TimelineListener {
        fun onSeek(positionMs: Long)
        fun onClipSelected(clipId: String?)
        fun onClipTrimmed(clipId: String, startMs: Long, endMs: Long)
    }

    var listener: TimelineListener? = null

    private val clipPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val audioPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val thumbPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xCCFFFFFF.toInt()
        textSize = 28f
        textAlign = Paint.Align.LEFT
    }
    private val borderPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 2f
    }

    private var clips: List<VideoClip> = emptyList()
    private var audioTracks: List<AudioTrack> = emptyList()
    private var currentPositionMs: Long = 0L
    private var totalDurationMs: Long = 0L
    private var selectedClipId: String? = null

    private var scrollOffsetX = 0f
    private var pixelsPerMs = 0.1f
    private val minPixelsPerMs = 0.02f
    private val maxPixelsPerMs = 1.0f

    private val trackHeight = context.resources.getDimensionPixelSize(R.dimen.timeline_track_height).toFloat()
    private val audioTrackHeight = context.resources.getDimensionPixelSize(R.dimen.timeline_audio_track_height).toFloat()
    private val thumbWidth = context.resources.getDimensionPixelSize(R.dimen.timeline_thumb_width).toFloat()
    private val centerPad = context.resources.getDimensionPixelSize(R.dimen.timeline_padding_horizontal).toFloat()

    private val clipBgColor = ContextCompat.getColor(context, R.color.timeline_clip_bg)
    private val audioBgColor = ContextCompat.getColor(context, R.color.timeline_audio_bg)
    private val selectedColor = ContextCompat.getColor(context, R.color.timeline_selection)
    private val borderColor = ContextCompat.getColor(context, R.color.timeline_border)

    private val gestureDetector = GestureDetector(context, object : GestureDetector.SimpleOnGestureListener() {
        override fun onScroll(e1: MotionEvent?, e2: MotionEvent, distanceX: Float, distanceY: Float): Boolean {
            scrollOffsetX = (scrollOffsetX - distanceX).coerceIn(
                -(totalDurationMs * pixelsPerMs),
                centerPad
            )
            updatePositionFromScroll()
            invalidate()
            return true
        }

        override fun onSingleTapUp(e: MotionEvent): Boolean {
            val timeMs = screenXToMs(e.x)
            listener?.onSeek(timeMs.coerceIn(0, totalDurationMs))
            val tappedClip = getClipAtTime(timeMs)
            selectedClipId = tappedClip?.id
            listener?.onClipSelected(selectedClipId)
            invalidate()
            return true
        }
    })

    private val scaleGestureDetector = ScaleGestureDetector(context, object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
        override fun onScale(detector: ScaleGestureDetector): Boolean {
            pixelsPerMs = (pixelsPerMs * detector.scaleFactor).coerceIn(minPixelsPerMs, maxPixelsPerMs)
            invalidate()
            return true
        }
    })

    init {
        clipPaint.color = clipBgColor
        audioPaint.color = audioBgColor
        borderPaint.color = borderColor
    }

    fun setData(clips: List<VideoClip>, audioTracks: List<AudioTrack> = emptyList(), totalDurationMs: Long) {
        this.clips = clips
        this.audioTracks = audioTracks
        this.totalDurationMs = totalDurationMs
        if (totalDurationMs > 0) {
            pixelsPerMs = (width - centerPad * 2) / totalDurationMs.toFloat()
            pixelsPerMs = pixelsPerMs.coerceIn(minPixelsPerMs, maxPixelsPerMs)
        }
        invalidate()
    }

    fun setCurrentPosition(positionMs: Long) {
        currentPositionMs = positionMs
        scrollOffsetX = centerPad - positionMs * pixelsPerMs
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        canvas.drawColor(ContextCompat.getColor(context, R.color.timeline_bg))

        val trackTop = (height - trackHeight - audioTrackHeight - 8) / 2f
        val audioTrackTop = trackTop + trackHeight + 8

        drawVideoClips(canvas, trackTop)
        drawAudioTracks(canvas, audioTrackTop)
        drawTimeRuler(canvas, trackTop - 20f)
    }

    private fun drawVideoClips(canvas: Canvas, trackTop: Float) {
        var xOffset = scrollOffsetX + centerPad
        clips.forEach { clip ->
            val clipWidthPx = clip.effectiveDurationMs * pixelsPerMs
            val rect = RectF(xOffset, trackTop, xOffset + clipWidthPx, trackTop + trackHeight)

            // Clip background
            clipPaint.color = if (clip.id == selectedClipId) 0xFF3D8B6A.toInt() else clipBgColor
            canvas.drawRoundRect(rect, 8f, 8f, clipPaint)

            // Draw thumbnails inside clip
            clip.thumbnails.forEachIndexed { index, bitmap ->
                val thumbX = xOffset + index * thumbWidth
                if (thumbX < rect.right && thumbX + thumbWidth > rect.left) {
                    val srcRect = android.graphics.Rect(0, 0, bitmap.width, bitmap.height)
                    val dstRect = RectF(
                        max(thumbX, rect.left),
                        trackTop + 2,
                        min(thumbX + thumbWidth, rect.right),
                        trackTop + trackHeight - 2
                    )
                    canvas.drawBitmap(bitmap, srcRect, dstRect, thumbPaint)
                }
            }

            // Border
            borderPaint.color = if (clip.id == selectedClipId) 0xFFFF6B35.toInt() else borderColor
            canvas.drawRoundRect(rect, 8f, 8f, borderPaint)

            // Clip name text
            val clipName = clip.name.take(12)
            canvas.drawText(clipName, xOffset + 8, trackTop + 22, textPaint)

            xOffset += clipWidthPx + 4
        }
    }

    private fun drawAudioTracks(canvas: Canvas, audioTop: Float) {
        audioTracks.forEach { track ->
            val startX = scrollOffsetX + centerPad + track.startOffsetMs * pixelsPerMs
            val endX = startX + (track.trimEndMs - track.trimStartMs) * pixelsPerMs
            val rect = RectF(startX, audioTop, endX, audioTop + audioTrackHeight)
            audioPaint.color = audioBgColor
            canvas.drawRoundRect(rect, 6f, 6f, audioPaint)
            borderPaint.color = borderColor
            canvas.drawRoundRect(rect, 6f, 6f, borderPaint)

            if (endX - startX > 60) {
                textPaint.textSize = 22f
                canvas.drawText(track.name.take(15), startX + 6, audioTop + audioTrackHeight - 8, textPaint)
                textPaint.textSize = 28f
            }
        }
    }

    private fun drawTimeRuler(canvas: Canvas, rulerY: Float) {
        val rulerPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = 0x88FFFFFF.toInt()
            textSize = 22f
            textAlign = Paint.Align.CENTER
        }
        val tickPaint = Paint().apply {
            color = 0x55FFFFFF.toInt()
            strokeWidth = 1f
        }
        val stepMs = when {
            pixelsPerMs > 0.5f -> 1000L
            pixelsPerMs > 0.2f -> 2000L
            pixelsPerMs > 0.1f -> 5000L
            else -> 10000L
        }
        var t = 0L
        while (t <= totalDurationMs) {
            val x = scrollOffsetX + centerPad + t * pixelsPerMs
            if (x in 0f..width.toFloat()) {
                canvas.drawLine(x, rulerY, x, rulerY + 12, tickPaint)
                canvas.drawText(VideoClip.formatMs(t), x, rulerY + 10, rulerPaint)
            }
            t += stepMs
        }
    }

    private fun updatePositionFromScroll() {
        val positionMs = ((centerPad - scrollOffsetX) / pixelsPerMs).toLong()
        currentPositionMs = positionMs.coerceIn(0, totalDurationMs)
        listener?.onSeek(currentPositionMs)
    }

    private fun screenXToMs(screenX: Float): Long {
        return ((screenX - scrollOffsetX - centerPad) / pixelsPerMs).toLong()
    }

    private fun getClipAtTime(timeMs: Long): VideoClip? {
        var cumulative = 0L
        clips.forEach { clip ->
            val end = cumulative + clip.effectiveDurationMs
            if (timeMs in cumulative..end) return clip
            cumulative = end
        }
        return null
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        scaleGestureDetector.onTouchEvent(event)
        if (!scaleGestureDetector.isInProgress) gestureDetector.onTouchEvent(event)
        return true
    }
}
