package com.codeea.videoeditor.timeline

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.codeea.videoeditor.R
import com.codeea.videoeditor.model.VideoFilter

class FilterAdapter(
    private val onFilterSelected: (VideoFilter) -> Unit
) : RecyclerView.Adapter<FilterAdapter.FilterViewHolder>() {

    private val filters = VideoFilter.values().toList()
    private var selectedFilter = VideoFilter.NONE

    fun setSelectedFilter(filter: VideoFilter) {
        val oldIndex = filters.indexOf(selectedFilter)
        selectedFilter = filter
        val newIndex = filters.indexOf(filter)
        notifyItemChanged(oldIndex)
        notifyItemChanged(newIndex)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): FilterViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_filter, parent, false)
        return FilterViewHolder(view)
    }

    override fun onBindViewHolder(holder: FilterViewHolder, position: Int) {
        val filter = filters[position]
        holder.bind(filter, filter == selectedFilter)
        holder.itemView.setOnClickListener {
            setSelectedFilter(filter)
            onFilterSelected(filter)
        }
    }

    override fun getItemCount() = filters.size

    inner class FilterViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        private val ivPreview: ImageView = view.findViewById(R.id.ivFilterPreview)
        private val tvName: TextView = view.findViewById(R.id.tvFilterName)
        private val viewSelected: View = view.findViewById(R.id.viewFilterSelected)

        fun bind(filter: VideoFilter, isSelected: Boolean) {
            tvName.text = filter.displayName
            viewSelected.visibility = if (isSelected) View.VISIBLE else View.GONE

            // Apply filter color tint as visual preview
            val tintColor = when (filter) {
                VideoFilter.COOL -> 0xFF6699CC.toInt()
                VideoFilter.WARM -> 0xFFCC9966.toInt()
                VideoFilter.NOIR -> 0xFF888888.toInt()
                VideoFilter.VIVID -> 0xFF66CC66.toInt()
                VideoFilter.VINTAGE -> 0xFFAA8866.toInt()
                VideoFilter.FADE -> 0xFFCCCCCC.toInt()
                VideoFilter.DRAMA -> 0xFF444466.toInt()
                VideoFilter.CINEMATIC -> 0xFF334455.toInt()
                else -> 0xFF555555.toInt()
            }
            ivPreview.setBackgroundColor(tintColor)
        }
    }
}
