package com.snmworks.mobile

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.jan.supabase.auth.auth
import kotlinx.coroutines.launch

@Composable
fun QCCheckFormScreen(
    onBack: () -> Unit,
    onSubmitSuccess: (QCCheckResponse) -> Unit
) {
    val scope = rememberCoroutineScope()
    var isLoadingOptions by remember { mutableStateOf(true) }
    var isSubmitting by remember { mutableStateOf(false) }
    var errorMessage by remember { mutableStateOf<String?>(null) }

    var jobs by remember { mutableStateOf<List<JobOption>>(emptyList()) }
    var stages by remember { mutableStateOf<List<String>>(emptyList()) }
    var limitKinds by remember { mutableStateOf<List<String>>(emptyList()) }

    var selectedJob by remember { mutableStateOf<JobOption?>(null) }
    var jobDropdownExpanded by remember { mutableStateOf(false) }

    var selectedStage by remember { mutableStateOf("On-Loom Inspection") }
    var stageDropdownExpanded by remember { mutableStateOf(false) }

    var selectedLimitType by remember { mutableStateOf("nominal") }
    var limitDropdownExpanded by remember { mutableStateOf(false) }

    var parameter by remember { mutableStateOf("Width") }
    var specValue by remember { mutableStateOf("44.0") }
    var actualValue by remember { mutableStateOf("44.0") }
    var unit by remember { mutableStateOf("mm") }

    fun loadOptions() {
        isLoadingOptions = true
        errorMessage = null
        scope.launch {
            val token = SupabaseClient.client.auth.currentAccessTokenOrNull()
            if (token == null) {
                errorMessage = "Authentication token expired. Please sign in again."
                isLoadingOptions = false
                return@launch
            }
            val result = QCApi.fetchOptions(token)
            result.onSuccess { options ->
                jobs = options.jobs
                stages = options.stages
                limitKinds = options.limitKinds
                if (options.jobs.isNotEmpty()) {
                    selectedJob = options.jobs.first()
                }
                if (options.stages.isNotEmpty()) {
                    selectedStage = options.stages.first()
                }
                if (options.limitKinds.isNotEmpty()) {
                    selectedLimitType = options.limitKinds.first()
                }
                isLoadingOptions = false
            }.onFailure { err ->
                errorMessage = err.message ?: "Failed to load options"
                isLoadingOptions = false
            }
        }
    }

    LaunchedEffect(Unit) {
        loadOptions()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(20.dp)
    ) {
        // Header
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text(
                    text = "NEW QC CHECK",
                    fontSize = 22.sp,
                    fontWeight = FontWeight.Bold,
                    color = SnmDark,
                    letterSpacing = 1.sp
                )
                Text(
                    text = "Shop-Floor Line Inspection",
                    fontSize = 13.sp,
                    color = Color.Gray
                )
            }
            OutlinedButton(
                onClick = onBack,
                shape = RoundedCornerShape(8.dp),
                modifier = Modifier.height(38.dp)
            ) {
                Text("Cancel", fontSize = 13.sp, color = SnmDark)
            }
        }

        Spacer(modifier = Modifier.height(16.dp))

        if (isLoadingOptions) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(200.dp),
                contentAlignment = Alignment.Center
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    CircularProgressIndicator(color = SnmOlive)
                    Spacer(modifier = Modifier.height(12.dp))
                    Text("Loading jobs and specs...", fontSize = 14.sp, color = Color.Gray)
                }
            }
            return
        }

        if (errorMessage != null) {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = Color(0xFFFFEBEE)),
                shape = RoundedCornerShape(8.dp)
            ) {
                Column(modifier = Modifier.padding(12.dp)) {
                    Text(
                        text = errorMessage ?: "",
                        color = SnmFail,
                        fontSize = 13.sp
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Button(
                        onClick = { loadOptions() },
                        colors = ButtonDefaults.buttonColors(containerColor = SnmFail),
                        shape = RoundedCornerShape(6.dp),
                        modifier = Modifier.height(36.dp)
                    ) {
                        Text("Retry", fontSize = 12.sp)
                    }
                }
            }
            Spacer(modifier = Modifier.height(16.dp))
        }

        // Job Selector
        Text("Production Job", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
        Spacer(modifier = Modifier.height(4.dp))
        Box(modifier = Modifier.fillMaxWidth()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color.White, RoundedCornerShape(8.dp))
                    .border(1.dp, Color.LightGray, RoundedCornerShape(8.dp))
                    .clickable { jobDropdownExpanded = true }
                    .padding(horizontal = 14.dp, vertical = 14.dp)
            ) {
                Text(
                    text = if (selectedJob != null) {
                        "${selectedJob?.jobNo} — ${selectedJob?.product}"
                    } else {
                        "Select Job (Optional)"
                    },
                    fontSize = 14.sp,
                    color = if (selectedJob != null) SnmDark else Color.Gray,
                    fontFamily = if (selectedJob != null) FontFamily.Monospace else FontFamily.Default
                )
            }
            DropdownMenu(
                expanded = jobDropdownExpanded,
                onDismissRequest = { jobDropdownExpanded = false }
            ) {
                jobs.forEach { job ->
                    DropdownMenuItem(
                        text = {
                            Column {
                                Text(job.jobNo, fontWeight = FontWeight.Bold, fontFamily = FontFamily.Monospace)
                                Text(job.product, fontSize = 12.sp, color = Color.Gray)
                            }
                        },
                        onClick = {
                            selectedJob = job
                            jobDropdownExpanded = false
                        }
                    )
                }
            }
        }

        Spacer(modifier = Modifier.height(14.dp))

        // Stage Selector
        Text("Inspection Stage", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
        Spacer(modifier = Modifier.height(4.dp))
        Box(modifier = Modifier.fillMaxWidth()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color.White, RoundedCornerShape(8.dp))
                    .border(1.dp, Color.LightGray, RoundedCornerShape(8.dp))
                    .clickable { stageDropdownExpanded = true }
                    .padding(horizontal = 14.dp, vertical = 14.dp)
            ) {
                Text(
                    text = selectedStage,
                    fontSize = 14.sp,
                    color = SnmDark
                )
            }
            DropdownMenu(
                expanded = stageDropdownExpanded,
                onDismissRequest = { stageDropdownExpanded = false }
            ) {
                stages.forEach { stg ->
                    DropdownMenuItem(
                        text = { Text(stg) },
                        onClick = {
                            selectedStage = stg
                            stageDropdownExpanded = false
                        }
                    )
                }
            }
        }

        Spacer(modifier = Modifier.height(14.dp))

        // Parameter Name & Unit in a row
        Row(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.weight(0.65f)) {
                Text("Parameter", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
                Spacer(modifier = Modifier.height(4.dp))
                OutlinedTextField(
                    value = parameter,
                    onValueChange = { parameter = it },
                    placeholder = { Text("e.g. Width") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !isSubmitting
                )
            }
            Spacer(modifier = Modifier.width(12.dp))
            Column(modifier = Modifier.weight(0.35f)) {
                Text("Unit", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
                Spacer(modifier = Modifier.height(4.dp))
                OutlinedTextField(
                    value = unit,
                    onValueChange = { unit = it },
                    placeholder = { Text("mm") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !isSubmitting
                )
            }
        }

        Spacer(modifier = Modifier.height(14.dp))

        // Limit Type Selector
        Text("Limit Type", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
        Spacer(modifier = Modifier.height(4.dp))
        Box(modifier = Modifier.fillMaxWidth()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color.White, RoundedCornerShape(8.dp))
                    .border(1.dp, Color.LightGray, RoundedCornerShape(8.dp))
                    .clickable { limitDropdownExpanded = true }
                    .padding(horizontal = 14.dp, vertical = 14.dp)
            ) {
                Text(
                    text = selectedLimitType.uppercase(),
                    fontSize = 14.sp,
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Medium,
                    color = SnmDark
                )
            }
            DropdownMenu(
                expanded = limitDropdownExpanded,
                onDismissRequest = { limitDropdownExpanded = false }
            ) {
                limitKinds.forEach { lk ->
                    DropdownMenuItem(
                        text = { Text(lk.uppercase(), fontFamily = FontFamily.Monospace) },
                        onClick = {
                            selectedLimitType = lk
                            limitDropdownExpanded = false
                        }
                    )
                }
            }
        }

        Spacer(modifier = Modifier.height(14.dp))

        // Spec Value & Actual Value in a row
        Row(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.weight(1f)) {
                Text("Spec Value", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
                Spacer(modifier = Modifier.height(4.dp))
                OutlinedTextField(
                    value = specValue,
                    onValueChange = { specValue = it },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !isSubmitting
                )
            }
            Spacer(modifier = Modifier.width(12.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text("Actual Reading", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
                Spacer(modifier = Modifier.height(4.dp))
                OutlinedTextField(
                    value = actualValue,
                    onValueChange = { actualValue = it },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !isSubmitting
                )
            }
        }

        Spacer(modifier = Modifier.height(24.dp))

        // Submit Button
        Button(
            onClick = {
                val spec = specValue.toDoubleOrNull()
                if (parameter.isBlank()) {
                    errorMessage = "Parameter name is required"
                    return@Button
                }
                if (spec == null) {
                    errorMessage = "Valid numeric spec value is required"
                    return@Button
                }

                val actual = actualValue.toDoubleOrNull()
                isSubmitting = true
                errorMessage = null

                scope.launch {
                    val token = SupabaseClient.client.auth.currentAccessTokenOrNull()
                    if (token == null) {
                        errorMessage = "Session expired. Please sign in again."
                        isSubmitting = false
                        return@launch
                    }

                    val req = QCCheckRequest(
                        jobId = selectedJob?.id,
                        stage = selectedStage,
                        parameter = parameter.trim(),
                        unit = unit.trim().ifEmpty { null },
                        limitType = selectedLimitType,
                        specValue = spec,
                        actual = actual
                    )

                    val result = QCApi.submitCheck(req, token)
                    result.onSuccess { resp ->
                        isSubmitting = false
                        onSubmitSuccess(resp)
                    }.onFailure { err ->
                        isSubmitting = false
                        errorMessage = err.message ?: "Failed to submit check"
                    }
                }
            },
            modifier = Modifier
                .fillMaxWidth()
                .height(50.dp),
            shape = RoundedCornerShape(8.dp),
            colors = ButtonDefaults.buttonColors(containerColor = SnmOlive),
            enabled = !isSubmitting
        ) {
            if (isSubmitting) {
                CircularProgressIndicator(
                    color = Color.White,
                    modifier = Modifier.size(24.dp),
                    strokeWidth = 2.dp
                )
            } else {
                Text(
                    text = "Submit Inspection",
                    fontSize = 16.sp,
                    fontWeight = FontWeight.SemiBold
                )
            }
        }

        Spacer(modifier = Modifier.height(24.dp))
    }
}

@Composable
fun QCResultScreen(
    result: QCCheckResponse,
    onNewCheck: () -> Unit,
    onDone: () -> Unit
) {
    val isPass = result.verdict.uppercase() == "PASS"
    val verdictColor = if (isPass) SnmPass else SnmFail
    val verdictBg = if (isPass) Color(0xFFE8F5E9) else Color(0xFFFFEBEE)

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(16.dp),
            colors = CardDefaults.cardColors(containerColor = SnmGreige)
        ) {
            Column(
                modifier = Modifier.padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text(
                    text = "INSPECTION RECORDED",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    color = Color.Gray,
                    letterSpacing = 1.sp
                )

                Spacer(modifier = Modifier.height(12.dp))

                Text(
                    text = result.checkNo,
                    fontSize = 32.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace,
                    color = SnmDark
                )

                Spacer(modifier = Modifier.height(16.dp))

                Box(
                    modifier = Modifier
                        .background(verdictBg, RoundedCornerShape(8.dp))
                        .border(2.dp, verdictColor, RoundedCornerShape(8.dp))
                        .padding(horizontal = 24.dp, vertical = 8.dp)
                ) {
                    Text(
                        text = result.verdict.uppercase(),
                        fontSize = 24.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace,
                        color = verdictColor,
                        letterSpacing = 2.sp
                    )
                }

                Spacer(modifier = Modifier.height(20.dp))

                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(Color.White, RoundedCornerShape(8.dp))
                        .padding(14.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text("Parameter:", fontSize = 13.sp, color = Color.Gray)
                        Text(
                            result.parameter,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold,
                            color = SnmDark
                        )
                    }
                    Spacer(modifier = Modifier.height(6.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text("Spec Value:", fontSize = 13.sp, color = Color.Gray)
                        Text(
                            "${result.specValue} ${result.unit ?: ""}".trim(),
                            fontSize = 13.sp,
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Medium,
                            color = SnmDark
                        )
                    }
                    Spacer(modifier = Modifier.height(6.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text("Actual Reading:", fontSize = 13.sp, color = Color.Gray)
                        Text(
                            "${result.actual ?: "—"} ${result.unit ?: ""}".trim(),
                            fontSize = 13.sp,
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold,
                            color = verdictColor
                        )
                    }
                }
            }
        }

        Spacer(modifier = Modifier.height(28.dp))

        Button(
            onClick = onNewCheck,
            modifier = Modifier
                .fillMaxWidth()
                .height(48.dp),
            shape = RoundedCornerShape(8.dp),
            colors = ButtonDefaults.buttonColors(containerColor = SnmOlive)
        ) {
            Text("Record Another Check", fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
        }

        Spacer(modifier = Modifier.height(12.dp))

        OutlinedButton(
            onClick = onDone,
            modifier = Modifier
                .fillMaxWidth()
                .height(48.dp),
            shape = RoundedCornerShape(8.dp)
        ) {
            Text("Back to Dashboard", fontSize = 15.sp, color = SnmDark)
        }
    }
}
