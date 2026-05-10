package com.codeea.videoeditor.ui.home

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.codeea.videoeditor.R
import com.codeea.videoeditor.model.EditProject
import java.text.SimpleDateFormat
import java.util.Locale

class ProjectAdapter(
    private val onProjectClick: (EditProject) -> Unit,
    private val onProjectOptions: (EditProject) -> Unit
) : RecyclerView.Adapter<ProjectAdapter.ProjectViewHolder>() {

    private val projects = mutableListOf<EditProject>()

    fun setProjects(list: List<EditProject>) {
        projects.clear()
        projects.addAll(list)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ProjectViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_project, parent, false)
        return ProjectViewHolder(view)
    }

    override fun onBindViewHolder(holder: ProjectViewHolder, position: Int) {
        holder.bind(projects[position])
    }

    override fun getItemCount() = projects.size

    inner class ProjectViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        private val ivThumbnail: ImageView = view.findViewById(R.id.ivProjectThumbnail)
        private val tvName: TextView = view.findViewById(R.id.tvProjectName)
        private val tvDate: TextView = view.findViewById(R.id.tvProjectDate)
        private val tvDuration: TextView = view.findViewById(R.id.tvProjectDuration)
        private val btnOptions: View = view.findViewById(R.id.btnProjectOptions)

        private val dateFormatter = SimpleDateFormat("MMM d, h:mm a", Locale.getDefault())

        fun bind(project: EditProject) {
            tvName.text = project.name
            tvDate.text = dateFormatter.format(project.updatedAt)
            tvDuration.text = project.displayDuration

            itemView.setOnClickListener { onProjectClick(project) }
            btnOptions.setOnClickListener { onProjectOptions(project) }
        }
    }
}
