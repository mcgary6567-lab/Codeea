package com.codeea.videoeditor.ui.home

import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.View
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.recyclerview.widget.LinearLayoutManager
import com.codeea.videoeditor.databinding.ActivityMainBinding
import com.codeea.videoeditor.model.EditProject
import com.codeea.videoeditor.ui.editor.EditorActivity
import com.codeea.videoeditor.utils.PermissionUtils
import java.util.UUID

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var projectAdapter: ProjectAdapter

    private val recentProjects = mutableListOf<EditProject>()

    private val pickVideoLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            result.data?.data?.let { uri ->
                contentResolver.takePersistableUriPermission(
                    uri, Intent.FLAG_GRANT_READ_URI_PERMISSION
                )
                openEditorWithVideo(uri)
            }
        }
    }

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { permissions ->
        val granted = permissions.values.all { it }
        if (granted) {
            launchVideoPicker()
        } else {
            showPermissionRationale()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        setupRecyclerView()
        setupClickListeners()
        updateProjectsList()
    }

    private fun setupRecyclerView() {
        projectAdapter = ProjectAdapter(
            onProjectClick = { project -> openExistingProject(project) },
            onProjectOptions = { project -> showProjectOptions(project) }
        )
        binding.rvRecentProjects.apply {
            layoutManager = LinearLayoutManager(this@MainActivity)
            adapter = projectAdapter
        }
    }

    private fun setupClickListeners() {
        binding.cardNewProject.setOnClickListener { checkPermissionsAndPick() }
        binding.fabNewProject.setOnClickListener { checkPermissionsAndPick() }
    }

    private fun checkPermissionsAndPick() {
        if (PermissionUtils.hasVideoPermission(this)) {
            launchVideoPicker()
        } else {
            permissionLauncher.launch(PermissionUtils.videoPermissions)
        }
    }

    private fun launchVideoPicker() {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            type = "video/*"
            addCategory(Intent.CATEGORY_OPENABLE)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
        }
        pickVideoLauncher.launch(intent)
    }

    private fun openEditorWithVideo(uri: Uri) {
        val project = EditProject(
            id = UUID.randomUUID().toString(),
            name = "New Project"
        )
        EditorActivity.start(this, project, uri)
    }

    private fun openExistingProject(project: EditProject) {
        EditorActivity.startWithProject(this, project)
    }

    private fun showProjectOptions(project: EditProject) {
        AlertDialog.Builder(this)
            .setTitle(project.name)
            .setItems(arrayOf("Rename", "Delete")) { _, which ->
                when (which) {
                    0 -> renameProject(project)
                    1 -> deleteProject(project)
                }
            }
            .show()
    }

    private fun renameProject(project: EditProject) {
        val input = android.widget.EditText(this).apply {
            setText(project.name)
            selectAll()
        }
        AlertDialog.Builder(this)
            .setTitle("Rename Project")
            .setView(input)
            .setPositiveButton("Rename") { _, _ ->
                val newName = input.text.toString().trim()
                if (newName.isNotEmpty()) {
                    val index = recentProjects.indexOfFirst { it.id == project.id }
                    if (index >= 0) {
                        recentProjects[index] = project.copy(name = newName)
                        updateProjectsList()
                    }
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun deleteProject(project: EditProject) {
        AlertDialog.Builder(this)
            .setTitle("Delete Project")
            .setMessage("Are you sure you want to delete \"${project.name}\"?")
            .setPositiveButton("Delete") { _, _ ->
                recentProjects.removeAll { it.id == project.id }
                updateProjectsList()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun updateProjectsList() {
        val isEmpty = recentProjects.isEmpty()
        binding.layoutEmptyState.visibility = if (isEmpty) View.VISIBLE else View.GONE
        binding.rvRecentProjects.visibility = if (isEmpty) View.GONE else View.VISIBLE
        projectAdapter.setProjects(recentProjects)
    }

    private fun showPermissionRationale() {
        AlertDialog.Builder(this)
            .setTitle("Permission Required")
            .setMessage("Video access permission is required to import videos for editing.")
            .setPositiveButton("Settings") { _, _ ->
                val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                    data = Uri.fromParts("package", packageName, null)
                }
                startActivity(intent)
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQUEST_CODE_EDITOR && resultCode == Activity.RESULT_OK) {
            data?.getSerializableExtra(EditorActivity.EXTRA_PROJECT)?.let { project ->
                val editProject = project as EditProject
                val index = recentProjects.indexOfFirst { it.id == editProject.id }
                if (index >= 0) {
                    recentProjects[index] = editProject
                } else {
                    recentProjects.add(0, editProject)
                }
                updateProjectsList()
            }
        }
    }

    companion object {
        const val REQUEST_CODE_EDITOR = 1001
    }
}
