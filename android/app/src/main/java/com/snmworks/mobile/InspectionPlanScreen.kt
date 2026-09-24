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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.jan.supabase.auth.auth
import kotlinx.coroutines.launch

@Composable
fun InspectionPlanScreen(
    job: JobItem,
    onBack: () -> Unit,
    onRecordCheckForRequirement: (JobItem, InspectionPlanItem) -> Unit
) {
    val scope = rememberCoroutineScope()
    var isLoading by remember { mutableStateOf(true) }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    var planResponse by remember { mutableStateOf<InspectionPlanResponse?>(null) }
    var selectedVariantId by remember { mutableStateOf<String?>(job.variantId) }
    var variantDropdownExpanded by remember { mutableStateOf(false) }

    fun loadPlan(variantId: String? = selectedVariantId) {
        isLoading = true
        errorMessage = null
        scope.launch {
            val token = SupabaseClient.client.auth.currentAccessTokenOrNull()
            if (token == null) {
                errorMessage = "Authentication token expired. Please sign in again."
                isLoading = false
                return@launch
            }
            val result = JobsApi.fetchInspectionPlan(token, job.id, variantId)
            result.onSuccess { resp ->
                planResponse = resp
                selectedVariantId = resp.selectedVariant?.id
                isLoading = false
            }.onFailure { err ->
                errorMessage = err.message ?: "Failed to load inspection plan"
                isLoading = false
            }
        }
    }

    LaunchedEffect(job.id) {
        loadPlan()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
    ) {
        // Header
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text(
                    text = "INSPECTION PLAN",
                    fontSize = 20.sp,
                    fontWeight = FontWeight.Bold,
                    color = SnmDark,
                    letterSpacing = 1.sp
                )
                Text(
                    text = "${job.jobNo} • ${job.product}",
                    fontSize = 13.sp,
                    color = Color.Gray,
                    fontFamily = FontFamily.Monospace
                )
            }
            OutlinedButton(
                onClick = onBack,
                shape = RoundedCornerShape(8.dp),
                modifier = Modifier.height(36.dp)
            ) {
                Text("Back", fontSize = 12.sp, color = SnmDark)
            }
        }

        Spacer(modifier = Modifier.height(12.dp))

        if (isLoading) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f),
                contentAlignment = Alignment.Center
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    CircularProgressIndicator(color = SnmOlive)
                    Spacer(modifier = Modifier.height(12.dp))
                    Text("Loading inspection plan checkpoints...", fontSize = 14.sp, color = Color.Gray)
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
                        onClick = { loadPlan() },
                        colors = ButtonDefaults.buttonColors(containerColor = SnmFail),
                        shape = RoundedCornerShape(6.dp),
                        modifier = Modifier.height(36.dp)
                    ) {
                        Text("Retry", fontSize = 12.sp)
                    }
                }
            }
            Spacer(modifier = Modifier.height(12.dp))
        }

        val resp = planResponse
        if (resp != null) {
            // Specification Variant Selector Card
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = SnmGreige.copy(alpha = 0.6f)),
                shape = RoundedCornerShape(8.dp)
            ) {
                Column(modifier = Modifier.padding(12.dp)) {
                    Text("Specification Variant", fontSize = 12.sp, fontWeight = FontWeight.Bold, color = SnmDark)
                    Spacer(modifier = Modifier.height(4.dp))
                    Box(modifier = Modifier.fillMaxWidth()) {
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .background(Color.White, RoundedCornerShape(6.dp))
                                .border(1.dp, Color.LightGray, RoundedCornerShape(6.dp))
                                .clickable { variantDropdownExpanded = true }
                                .padding(horizontal = 12.dp, vertical = 10.dp)
                        ) {
                            Text(
                                text = resp.selectedVariant?.designation ?: "Select Variant...",
                                fontSize = 13.sp,
                                fontWeight = FontWeight.Medium,
                                color = if (resp.selectedVariant != null) SnmDark else Color.Gray
                            )
                        }
                        DropdownMenu(
                            expanded = variantDropdownExpanded,
                            onDismissRequest = { variantDropdownExpanded = false }
                        ) {
                            resp.availableVariants.forEach { v ->
                                DropdownMenuItem(
                                    text = { Text(v.designation, fontWeight = FontWeight.Medium) },
                                    onClick = {
                                        selectedVariantId = v.id
                                        variantDropdownExpanded = false
                                        loadPlan(v.id)
                                    }
                                )
                            }
                        }
                    }
                }
            }

            Spacer(modifier = Modifier.height(10.dp))

            // Summary metrics row
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color.White, RoundedCornerShape(8.dp))
                    .border(1.dp, Color(0xFFE0DDD5), RoundedCornerShape(8.dp))
                    .padding(10.dp),
                horizontalArrangement = Arrangement.SpaceAround
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("TOTAL", fontSize = 10.sp, color = Color.Gray, fontWeight = FontWeight.Bold)
                    Text(
                        "${resp.summary.totalItems}",
                        fontSize = 16.sp,
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold,
                        color = SnmDark
                    )
                }
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("CHECKED", fontSize = 10.sp, color = SnmPass, fontWeight = FontWeight.Bold)
                    Text(
                        "${resp.summary.checkedItems}",
                        fontSize = 16.sp,
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold,
                        color = SnmPass
                    )
                }
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("PENDING", fontSize = 10.sp, color = Color(0xFFC77700), fontWeight = FontWeight.Bold)
                    Text(
                        "${resp.summary.pendingItems}",
                        fontSize = 16.sp,
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold,
                        color = Color(0xFFC77700)
                    )
                }
            }

            Spacer(modifier = Modifier.height(12.dp))

            if (resp.planItems.isEmpty()) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        text = if (resp.requiresVariantSelection) {
                            "Please select an approved specification variant to load checklist items."
                        } else {
                            "No checklist items defined for this specification variant."
                        },
                        fontSize = 14.sp,
                        color = Color.Gray
                    )
                }
            } else {
                LazyColumn(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    items(resp.planItems, key = { it.parameter }) { item ->
                        InspectionChecklistItem(
                            item = item,
                            onRecordCheck = { onRecordCheckForRequirement(job, item) }
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun InspectionChecklistItem(
    item: InspectionPlanItem,
    onRecordCheck: () -> Unit
) {
    val isChecked = item.isChecked
    val verdict = item.latestVerdict
    val isPass = verdict?.uppercase() == "PASS"

    val statusBg = when {
        !isChecked -> Color(0xFFFFF8E1)
        isPass -> Color(0xFFE8F5E9)
        else -> Color(0xFFFFEBEE)
    }

    val statusBorder = when {
        !isChecked -> Color(0xFFFFC107)
        isPass -> SnmPass
        else -> SnmFail
    }

    val statusText = when {
        !isChecked -> "PENDING"
        isPass -> "PASS"
        else -> "FAIL"
    }

    val statusTextColor = when {
        !isChecked -> Color(0xFFB78103)
        isPass -> SnmPass
        else -> SnmFail
    }

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, Color(0xFFDCD8CE), RoundedCornerShape(8.dp)),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(containerColor = Color.White)
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            text = item.parameter,
                            fontSize = 15.sp,
                            fontWeight = FontWeight.Bold,
                            color = SnmDark
                        )
                        if (item.isCritical) {
                            Spacer(modifier = Modifier.width(6.dp))
                            Box(
                                modifier = Modifier
                                    .background(Color(0xFFFFEBEE), RoundedCornerShape(4.dp))
                                    .padding(horizontal = 5.dp, vertical = 2.dp)
                            ) {
                                Text("CRITICAL", fontSize = 9.sp, fontWeight = FontWeight.Bold, color = SnmFail)
                            }
                        }
                    }
                    if (!item.stage.isNullOrBlank()) {
                        Text(
                            text = item.stage,
                            fontSize = 11.sp,
                            color = Color.Gray
                        )
                    }
                }

                Box(
                    modifier = Modifier
                        .background(statusBg, RoundedCornerShape(6.dp))
                        .border(1.dp, statusBorder, RoundedCornerShape(6.dp))
                        .padding(horizontal = 10.dp, vertical = 4.dp)
                ) {
                    Text(
                        text = statusText,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace,
                        color = statusTextColor
                    )
                }
            }

            Spacer(modifier = Modifier.height(8.dp))

            // Spec Value Line
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(
                    text = "Target: ${item.limitType.uppercase()} ${item.specValue ?: ""} ${item.unit ?: ""}".trim(),
                    fontSize = 12.sp,
                    fontFamily = FontFamily.Monospace,
                    color = Color.DarkGray
                )
                if (isChecked && item.matchedChecks.isNotEmpty()) {
                    val latest = item.matchedChecks.first()
                    Text(
                        text = "Actual: ${latest.actual ?: "—"} ${item.unit ?: ""}".trim(),
                        fontSize = 12.sp,
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold,
                        color = statusTextColor
                    )
                }
            }

            Spacer(modifier = Modifier.height(10.dp))

            Button(
                onClick = onRecordCheck,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(36.dp),
                shape = RoundedCornerShape(6.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (!isChecked) SnmOlive else SnmDark
                )
            ) {
                Text(
                    text = if (!isChecked) "Record QC Check" else "Record Additional Check",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold
                )
            }
        }
    }
}
