"""
Celery tasks for processing GIS files and managing embeddings
"""
from celery import shared_task
from django.contrib.auth import get_user_model
from .models import File, Folder
from .gis_utils import process_gis_file, publish_to_geoserver, bundle_and_publish_shapefile
import logging

logger = logging.getLogger(__name__)
User = get_user_model()

@shared_task(bind=True)
def process_gis_file_task(self, file_id):
    """
    Process a GIS file in the background
    """
    try:
        file_obj = File.objects.get(id=file_id)
        
        # Update status to processing
        file_obj.gis_status = 'processing'
        file_obj.processing_log = "Starting GIS file processing..."
        file_obj.save()
        
        # Process the file
        success, message = process_gis_file(file_obj)
        
        if success:
            file_obj.processing_log += f"\n✓ Processing completed: {message}"
            file_obj.save()
            
            # Trigger publishing to GeoServer
            publish_to_geoserver_task.delay(file_id)
            
            return f"Successfully processed GIS file: {file_obj.name}"
        else:
            file_obj.gis_status = 'error'
            file_obj.processing_log += f"\n✗ Processing failed: {message}"
            file_obj.save()
            
            return f"Failed to process GIS file: {message}"
            
    except File.DoesNotExist:
        return f"File with ID {file_id} not found"
    except Exception as e:
        logger.error(f"Error in process_gis_file_task: {str(e)}")
        try:
            file_obj = File.objects.get(id=file_id)
            file_obj.gis_status = 'error'
            file_obj.processing_log += f"\n✗ Task error: {str(e)}"
            file_obj.save()
        except:
            pass
        return f"Error processing GIS file: {str(e)}"

@shared_task(bind=True)
def publish_to_geoserver_task(self, file_id):
    """
    Publish processed GIS file to GeoServer
    """
    try:
        file_obj = File.objects.get(id=file_id)
        
        # Check if file is processed
        if file_obj.gis_status != 'processed':
            return f"File {file_obj.name} is not in processed state"
        
        # Check if this is a shapefile that needs bundling
        if file_obj.name.lower().endswith('.shp'):
            logger.info(f"Delegating shapefile to delayed publishing task: {file_obj.name}")
            # Use delayed task to allow all components to upload first
            delayed_shapefile_publish_task.apply_async(args=[file_id], countdown=30)  # Wait 30 seconds
            return f"Scheduled delayed publishing for shapefile: {file_obj.name}"
        
        # Use regular publishing for other file types
        success, message = publish_to_geoserver(file_obj)
        
        if success:
            file_obj.processing_log += f"\n✓ Published to GeoServer: {message}"
            file_obj.save()
            return f"Successfully published to GeoServer: {file_obj.name}"
        else:
            file_obj.gis_status = 'error'
            file_obj.processing_log += f"\n✗ Publishing failed: {message}"
            file_obj.save()
            return f"Failed to publish to GeoServer: {message}"
            
    except File.DoesNotExist:
        return f"File with ID {file_id} not found"
    except Exception as e:
        logger.error(f"Error in publish_to_geoserver_task: {str(e)}")
        try:
            file_obj = File.objects.get(id=file_id)
            file_obj.gis_status = 'error'
            file_obj.processing_log += f"\n✗ Publishing error: {str(e)}"
            file_obj.save()
        except:
            pass
        return f"Error publishing to GeoServer: {str(e)}"

@shared_task
def process_folder_gis_files(folder_id):
    """
    Process all GIS files in a folder (when a folder is uploaded as ZIP)
    """
    try:
        from .models import Folder
        
        folder = Folder.objects.get(id=folder_id)
        gis_files = folder.files.filter(is_spatial=True, gis_status='pending')
        
        processed_count = 0
        
        for file_obj in gis_files:
            # Trigger processing for each GIS file
            process_gis_file_task.delay(str(file_obj.id))
            processed_count += 1
        
        return f"Triggered processing for {processed_count} GIS files in folder {folder.name}"
        
    except Exception as e:
        logger.error(f"Error in process_folder_gis_files: {str(e)}")
        return f"Error processing folder GIS files: {str(e)}"

@shared_task(bind=True)
def delayed_shapefile_publish_task(self, file_id, max_retries=5):
    """
    Delayed task to publish shapefiles after allowing time for all components to upload
    """
    try:
        file_obj = File.objects.get(id=file_id)
        
        # Check if file is still in processed state (not already published)
        if file_obj.gis_status != 'processed':
            logger.info(f"Shapefile {file_obj.name} is no longer in processed state: {file_obj.gis_status}")
            return f"Shapefile {file_obj.name} status: {file_obj.gis_status}"
        
        # Check if this is a shapefile
        if not file_obj.name.lower().endswith('.shp'):
            logger.warning(f"File {file_obj.name} is not a shapefile, using regular publishing")
            return publish_to_geoserver_task.delay(file_id)
        
        # Get the base name and check for components
        base_name = file_obj.name.replace('.shp', '')
        required_exts = ['.shp', '.shx', '.dbf']
        
        # Find all related components
        components = File.objects.filter(
            owner=file_obj.owner,
            name__startswith=base_name,
            folder=file_obj.folder
        )
        
        # Check if we have all required components
        available_exts = set()
        for comp in components:
            parts = comp.name.split('.')
            if len(parts) >= 2:
                ext = '.' + parts[-1].lower()
                available_exts.add(ext)
        
        missing_components = [ext for ext in required_exts if ext not in available_exts]
        
        if missing_components:
            # If we're missing components and haven't hit max retries, retry later
            if self.request.retries < max_retries:
                logger.info(f"Missing components {missing_components} for {file_obj.name}, retrying in 10 seconds (attempt {self.request.retries + 1}/{max_retries})")
                raise self.retry(countdown=10, max_retries=max_retries)
            else:
                # Max retries reached, log error and continue with what we have
                logger.error(f"Max retries reached for {file_obj.name}, missing components: {missing_components}")
                file_obj.gis_status = 'error'
                file_obj.processing_log += f"\n✗ Missing shapefile components after {max_retries} retries: {', '.join(missing_components)}"
                file_obj.save()
                return f"Failed to find all components for {file_obj.name}"
        
        # All components available, proceed with bundling and publishing
        logger.info(f"All components available for {file_obj.name}, proceeding with bundling")
        success, message = bundle_and_publish_shapefile(file_obj)
        
        if success:
            file_obj.processing_log += f"\n✓ Auto-published to GeoServer: {message}"
            file_obj.save()
            logger.info(f"Successfully auto-published shapefile: {file_obj.name}")
            return f"Successfully auto-published shapefile: {file_obj.name}"
        else:
            file_obj.gis_status = 'error'
            file_obj.processing_log += f"\n✗ Auto-publishing failed: {message}"
            file_obj.save()
            logger.error(f"Failed to auto-publish shapefile: {file_obj.name} - {message}")
            return f"Failed to auto-publish shapefile: {message}"
            
    except File.DoesNotExist:
        return f"File with ID {file_id} not found"
    except Exception as e:
        if 'retry' not in str(e):  # Don't log retry exceptions
            logger.error(f"Error in delayed_shapefile_publish_task: {str(e)}")
        raise  # Re-raise for Celery to handle retries

# NOTE: File embedding generation task removed - no longer needed
# Search now uses PostgreSQL text matching instead of embeddings

# NOTE: Folder embedding generation task removed - no longer needed
# Search now uses PostgreSQL text matching instead of embeddings

# NOTE: Recursive embedding generation task removed - no longer needed
# Search now uses PostgreSQL text matching instead of embeddings

# NOTE: All embedding-related tasks removed - no longer needed
# Search now uses PostgreSQL text matching instead of embeddings


@shared_task(bind=True)
def delete_file_async_task(self, file_id, file_name):
    """
    Asynchronously delete a file with complete cleanup of all dependencies
    """
    import django
    django.setup()
    
    try:
        from django.apps import apps
        File = apps.get_model('filemanager', 'File')
        
        logger.info(f"Starting async deletion of file: {file_name} (ID: {file_id})")
        
        # Get the file object
        try:
            file_obj = File.objects.get(id=file_id)
        except File.DoesNotExist:
            logger.warning(f"File {file_name} (ID: {file_id}) not found - may have been already deleted")
            return f"File {file_name} not found - may have been already deleted"
        
        # Perform complete file deletion
        from .views import delete_file_complete
        delete_file_complete(file_obj)
        
        logger.info(f"Successfully completed async deletion of file: {file_name}")
        return f"Successfully deleted file: {file_name}"
        
    except Exception as e:
        logger.error(f"Error in async file deletion for {file_name}: {str(e)}")
        return f"Error deleting file {file_name}: {str(e)}"


@shared_task(bind=True)
def delete_folder_async_task(self, folder_id, folder_name):
    """
    Asynchronously delete a folder with complete recursive cleanup of all dependencies
    """
    import django
    django.setup()
    
    try:
        from django.apps import apps
        Folder = apps.get_model('filemanager', 'Folder')
        
        logger.info(f"Starting async deletion of folder: {folder_name} (ID: {folder_id})")
        
        # Get the folder object
        try:
            folder_obj = Folder.objects.get(id=folder_id)
        except Folder.DoesNotExist:
            logger.warning(f"Folder {folder_name} (ID: {folder_id}) not found - may have been already deleted")
            return f"Folder {folder_name} not found - may have been already deleted"
        
        # Perform complete folder deletion
        from .views import delete_folder_complete
        delete_folder_complete(folder_obj)
        
        logger.info(f"Successfully completed async deletion of folder: {folder_name}")
        return f"Successfully deleted folder: {folder_name}"
        
    except Exception as e:
        logger.error(f"Error in async folder deletion for {folder_name}: {str(e)}")
        return f"Error deleting folder {folder_name}: {str(e)}"


@shared_task(bind=True)
def toggle_folder_visibility_recursive_task(self, folder_id, is_public, folder_name):
    """
    Asynchronously toggle folder visibility recursively for all subfolders and files
    """
    import django
    django.setup()
    
    try:
        from django.apps import apps
        Folder = apps.get_model('filemanager', 'Folder')
        File = apps.get_model('filemanager', 'File')
        
        logger.info(f"Starting async recursive visibility toggle for folder: {folder_name} (ID: {folder_id}) to {'public' if is_public else 'private'}")
        
        # Get the folder object
        try:
            folder_obj = Folder.objects.get(id=folder_id)
        except Folder.DoesNotExist:
            logger.warning(f"Folder {folder_name} (ID: {folder_id}) not found - may have been deleted")
            return f"Folder {folder_name} not found - may have been deleted"
        
        # Recursive function to update visibility (skip the main folder since it's already updated)
        def update_recursive_visibility(current_folder, visibility, skip_current=False):
            count = 0
            
            # Update current folder (skip if this is the main folder)
            if not skip_current:
                current_folder.is_public = visibility
                current_folder.save(update_fields=['is_public'])
                count += 1
                logger.info(f"Updated folder: {current_folder.name} to {'public' if visibility else 'private'}")
            
            # Update all files in current folder
            for file_obj in current_folder.files.all():
                file_obj.is_public = visibility
                file_obj.save(update_fields=['is_public'])
                count += 1
                logger.info(f"Updated file: {file_obj.name} to {'public' if visibility else 'private'}")
            
            # Recursively update all subfolders
            for subfolder in current_folder.subfolders.all():
                count += update_recursive_visibility(subfolder, visibility, skip_current=False)
            
            return count
        
        # Perform recursive update (skip main folder since view already updated it)
        total_updated = update_recursive_visibility(folder_obj, is_public, skip_current=True)
        
        # Note: Embedding updates will be handled separately if needed
        logger.info(f"Completed recursive visibility update for folder {folder_name}")
        
        success_message = f"Successfully updated visibility for {total_updated} items (folder + subfolders + files) in {folder_name}"
        logger.info(success_message)
        return success_message
        
    except Exception as e:
        error_message = f"Error in async visibility toggle for {folder_name}: {str(e)}"
        logger.error(error_message)
        return error_message


@shared_task(bind=True)
def toggle_file_visibility_task(self, file_id, is_public, file_name):
    """
    Asynchronously toggle file visibility and update embeddings
    """
    import django
    django.setup()
    
    try:
        from django.apps import apps
        File = apps.get_model('filemanager', 'File')
        
        logger.info(f"Starting async visibility toggle for file: {file_name} (ID: {file_id}) to {'public' if is_public else 'private'}")
        
        # Get the file object
        try:
            file_obj = File.objects.get(id=file_id)
        except File.DoesNotExist:
            logger.warning(f"File {file_name} (ID: {file_id}) not found - may have been deleted")
            return f"File {file_name} not found - may have been deleted"
        
        # Update file visibility
        file_obj.is_public = is_public
        file_obj.save(update_fields=['is_public'])
        
        # Note: Embedding updates will be handled separately if needed
        logger.info(f"Completed visibility update for file {file_name}")
        
        success_message = f"Successfully updated visibility for file {file_name} to {'public' if is_public else 'private'}"
        logger.info(success_message)
        return success_message
        
    except Exception as e:
        error_message = f"Error in async visibility toggle for {file_name}: {str(e)}"
        logger.error(error_message)
        return error_message


@shared_task(bind=True)
def run_seeding_tool_task(self, file_id, output_dir_id=None):
    """
    Run the Seeding Tool on a .shp or .gpkg file.
    
    This task:
    1. Reads the input Point layer
    2. Creates seeding polygons (buffered by swath/boom width)
    3. Creates boundary polygons
    4. Generates a summary CSV
    5. Saves output files in the specified output directory or creates 'seeding_tool_output' folder
    6. Creates File records for the output files in Django
    
    Args:
        file_id: ID of the input File object
        output_dir_id: Optional ID of the Folder object for output. If not specified,
                       creates a 'seeding_tool_output' subdirectory in the input file's folder.
    """
    from django.conf import settings
    import os
    
    try:
        logger.info(f"Starting Seeding Tool task for file ID: {file_id}")
        
        # Get the file object
        try:
            file_obj = File.objects.get(id=file_id)
        except File.DoesNotExist:
            logger.error(f"File with ID {file_id} not found")
            return {"success": False, "error": f"File with ID {file_id} not found"}
        
        # Validate file type
        file_ext = os.path.splitext(file_obj.name)[1].lower()
        if file_ext not in ['.shp', '.gpkg']:
            error_msg = f"Seeding Tool only supports .shp and .gpkg files, got: {file_ext}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        
        # Get the input file path
        input_path = file_obj.file.path
        
        # Determine output directory and folder
        from .models import Folder
        output_folder_obj = None
        
        if output_dir_id:
            # Use specified output folder
            try:
                output_folder_obj = Folder.objects.get(id=output_dir_id)
                output_dir = output_folder_obj.get_full_path()
            except Folder.DoesNotExist:
                logger.error(f"Output folder with ID {output_dir_id} not found")
                return {"success": False, "error": f"Output folder with ID {output_dir_id} not found"}
        else:
            # Auto-create 'seeding_tool_output' folder under the input file's parent folder
            # First, create a Django Folder record
            parent_folder = file_obj.folder  # This is the input file's parent folder (can be None for root)
            
            # Check if a folder with this name already exists
            existing_folder = Folder.objects.filter(
                name="seeding_tool_output",
                parent=parent_folder,
                owner=file_obj.owner
            ).first()
            
            if existing_folder:
                output_folder_obj = existing_folder
                logger.info(f"Using existing 'seeding_tool_output' folder: {output_folder_obj.id}")
            else:
                # Create new folder record
                output_folder_obj = Folder.objects.create(
                    name="seeding_tool_output",
                    parent=parent_folder,
                    owner=file_obj.owner,
                    is_public=file_obj.is_public  # Inherit visibility from source file
                )
                logger.info(f"Created new 'seeding_tool_output' folder: {output_folder_obj.id}")
            
            output_dir = output_folder_obj.get_full_path()
        
        # Ensure output directory exists on filesystem
        os.makedirs(output_dir, exist_ok=True)
        
        logger.info(f"Processing file: {input_path}")
        logger.info(f"Output directory: {output_dir}")
        
        # Import and run the seeding tool
        from .SeedingTool_asappled_single_ADMA_V1 import process_seeding_tool
        
        success, message, output_files = process_seeding_tool(input_path, output_dir)
        
        if not success:
            logger.error(f"Seeding Tool failed: {message}")
            # Update file processing log
            file_obj.processing_log = (file_obj.processing_log or "") + f"\n✗ Seeding Tool failed: {message}"
            file_obj.save(update_fields=['processing_log'])
            return {"success": False, "error": message}
        
        logger.info(f"Seeding Tool completed: {message}")
        
        # Create File records for the output files
        created_files = []
        
        for file_type, file_path in output_files.items():
            if os.path.exists(file_path):
                try:
                    # Calculate relative path for Django FileField
                    media_root = settings.MEDIA_ROOT
                    if file_path.startswith(str(media_root)):
                        relative_path = os.path.relpath(file_path, media_root)
                    else:
                        relative_path = file_path
                    
                    file_name = os.path.basename(file_path)
                    file_size = os.path.getsize(file_path)
                    
                    # Check if file already exists (by name and folder)
                    existing_file = File.objects.filter(
                        name=file_name,
                        folder=output_folder_obj,
                        owner=file_obj.owner
                    ).first()
                    
                    if existing_file:
                        # Update existing file
                        existing_file.file_size = file_size
                        existing_file.save(update_fields=['file_size', 'updated_at'])
                        created_files.append({
                            'name': file_name,
                            'id': str(existing_file.id),
                            'updated': True
                        })
                        logger.info(f"Updated existing file: {file_name}")
                    else:
                        # Create new file record in the output folder
                        new_file = File(
                            name=file_name,
                            folder=output_folder_obj,
                            owner=file_obj.owner,
                            file_size=file_size,
                            is_public=file_obj.is_public,  # Inherit visibility from source file
                        )
                        # Set the file field to the relative path
                        new_file.file.name = relative_path
                        new_file.save()
                        
                        created_files.append({
                            'name': file_name,
                            'id': str(new_file.id),
                            'updated': False
                        })
                        logger.info(f"Created new file record: {file_name}")
                        
                except Exception as e:
                    logger.error(f"Error creating file record for {file_path}: {e}")
        
        # Update source file processing log
        file_obj.processing_log = (file_obj.processing_log or "") + f"\n✓ Seeding Tool completed: {message}"
        file_obj.save(update_fields=['processing_log'])
        
        result = {
            "success": True,
            "message": message,
            "created_files": created_files,
            "output_files": {k: os.path.basename(v) for k, v in output_files.items()}
        }
        
        logger.info(f"Seeding Tool task completed successfully: {result}")
        return result
        
    except Exception as e:
        logger.exception(f"Error in Seeding Tool task for file {file_id}")
        return {"success": False, "error": str(e)}


@shared_task(bind=True)
def run_shape_to_json_task(self, file_id, output_dir_id=None):
    """
    Convert a shapefile to GeoJSON format.
    
    This task:
    1. Reads the input shapefile
    2. Converts to WGS84 (EPSG:4326) for GeoJSON compatibility
    3. Saves the GeoJSON file
    4. Creates a File record for the output in Django
    
    Args:
        file_id: ID of the input File object
        output_dir_id: Optional ID of the Folder object for output. If not specified,
                       creates a 'geojson_output' subdirectory in the input file's folder.
    """
    from django.conf import settings
    import os
    
    try:
        logger.info(f"Starting Shape to JSON task for file ID: {file_id}")
        
        # Get the file object
        try:
            file_obj = File.objects.get(id=file_id)
        except File.DoesNotExist:
            logger.error(f"File with ID {file_id} not found")
            return {"success": False, "error": f"File with ID {file_id} not found"}
        
        # Validate file type
        file_ext = os.path.splitext(file_obj.name)[1].lower()
        if file_ext != '.shp':
            error_msg = f"Shape to JSON only supports .shp files, got: {file_ext}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        
        # Get the input file path
        input_path = file_obj.file.path
        
        # Determine output directory and folder
        from .models import Folder
        output_folder_obj = None
        
        if output_dir_id:
            # Use specified output folder
            try:
                output_folder_obj = Folder.objects.get(id=output_dir_id)
                output_dir = output_folder_obj.get_full_path()
            except Folder.DoesNotExist:
                logger.error(f"Output folder with ID {output_dir_id} not found")
                return {"success": False, "error": f"Output folder with ID {output_dir_id} not found"}
        else:
            # Auto-create 'geojson_output' folder under the input file's parent folder
            parent_folder = file_obj.folder
            
            # Check if a folder with this name already exists
            existing_folder = Folder.objects.filter(
                name="geojson_output",
                parent=parent_folder,
                owner=file_obj.owner
            ).first()
            
            if existing_folder:
                output_folder_obj = existing_folder
                logger.info(f"Using existing 'geojson_output' folder: {output_folder_obj.id}")
            else:
                # Create new folder record
                output_folder_obj = Folder.objects.create(
                    name="geojson_output",
                    parent=parent_folder,
                    owner=file_obj.owner,
                    is_public=file_obj.is_public
                )
                logger.info(f"Created new 'geojson_output' folder: {output_folder_obj.id}")
            
            output_dir = output_folder_obj.get_full_path()
        
        # Ensure output directory exists on filesystem
        os.makedirs(output_dir, exist_ok=True)
        
        logger.info(f"Processing file: {input_path}")
        logger.info(f"Output directory: {output_dir}")
        
        # Import and run the shape to json tool
        from .Shape_To_Json import process_shape_to_json
        
        success, message, output_files = process_shape_to_json(input_path, output_dir)
        
        if not success:
            logger.error(f"Shape to JSON failed: {message}")
            file_obj.processing_log = (file_obj.processing_log or "") + f"\n✗ Shape to JSON failed: {message}"
            file_obj.save(update_fields=['processing_log'])
            return {"success": False, "error": message}
        
        logger.info(f"Shape to JSON completed: {message}")
        
        # Create File records for the output files
        created_files = []
        
        for file_type, file_path in output_files.items():
            if os.path.exists(file_path):
                try:
                    media_root = settings.MEDIA_ROOT
                    if file_path.startswith(str(media_root)):
                        relative_path = os.path.relpath(file_path, media_root)
                    else:
                        relative_path = file_path
                    
                    file_name = os.path.basename(file_path)
                    file_size = os.path.getsize(file_path)
                    
                    # Check if file already exists
                    existing_file = File.objects.filter(
                        name=file_name,
                        folder=output_folder_obj,
                        owner=file_obj.owner
                    ).first()
                    
                    if existing_file:
                        existing_file.file_size = file_size
                        existing_file.save(update_fields=['file_size', 'updated_at'])
                        created_files.append({
                            'name': file_name,
                            'id': str(existing_file.id),
                            'updated': True
                        })
                        logger.info(f"Updated existing file: {file_name}")
                    else:
                        new_file = File(
                            name=file_name,
                            folder=output_folder_obj,
                            owner=file_obj.owner,
                            file_size=file_size,
                            is_public=file_obj.is_public,
                        )
                        new_file.file.name = relative_path
                        new_file.save()
                        
                        created_files.append({
                            'name': file_name,
                            'id': str(new_file.id),
                            'updated': False
                        })
                        logger.info(f"Created new file record: {file_name}")
                        
                except Exception as e:
                    logger.error(f"Error creating file record for {file_path}: {e}")
        
        # Update source file processing log
        file_obj.processing_log = (file_obj.processing_log or "") + f"\n✓ Shape to JSON completed: {message}"
        file_obj.save(update_fields=['processing_log'])
        
        result = {
            "success": True,
            "message": message,
            "created_files": created_files,
            "output_files": {k: os.path.basename(v) for k, v in output_files.items()}
        }
        
        logger.info(f"Shape to JSON task completed successfully: {result}")
        return result
        
    except Exception as e:
        logger.exception(f"Error in Shape to JSON task for file {file_id}")
        return {"success": False, "error": str(e)}


@shared_task(bind=True)
def run_si_tool_task(
    self,
    treatment,
    imagery,
    si_column_name,
    field_column,
    buffer_sectors_file_id,
    ndre_file_id=None,
    csv_file_id=None,
    indicator_block_file_id=None,
    output_dir_id=None
):
    """
    Run the SI (Stress Index) Tool.
    
    This task:
    1. Validates inputs based on treatment/imagery combination
    2. Runs the appropriate SI calculation workflow
    3. Updates the CSV with SI values
    4. Creates File records for output files
    
    Args:
        treatment: 'STANDARD' or 'SBF'
        imagery: 'UAV' or 'SATELLITE'
        si_column_name: Column name for SI values (e.g., 'SI_08_01')
        field_column: Field column for grouping (e.g., 'Plot_Numbe')
        buffer_sectors_file_id: ID of the buffer sectors shapefile
        ndre_file_id: ID of the NDRE shapefile (for UAV)
        csv_file_id: ID of the CSV file to update
        indicator_block_file_id: ID of indicator block shapefile (for SBF)
        output_dir_id: Optional ID of output folder
    """
    from django.conf import settings
    import os
    
    try:
        logger.info(f"Starting SI Tool task: {treatment} + {imagery}")
        
        # Get buffer sectors file (required for all workflows)
        try:
            buffer_file = File.objects.get(id=buffer_sectors_file_id)
            buffer_sectors_path = buffer_file.file.path
        except File.DoesNotExist:
            return {"success": False, "error": "Buffer sectors file not found"}
        
        # Get NDRE file (required for UAV)
        ndre_path = None
        if ndre_file_id:
            try:
                ndre_file = File.objects.get(id=ndre_file_id)
                ndre_path = ndre_file.file.path
            except File.DoesNotExist:
                return {"success": False, "error": "NDRE file not found"}
        
        # Get CSV file (required for all workflows)
        csv_path = None
        csv_file = None
        if csv_file_id:
            try:
                csv_file = File.objects.get(id=csv_file_id)
                csv_path = csv_file.file.path
            except File.DoesNotExist:
                return {"success": False, "error": "CSV file not found"}
        
        # Get indicator block file (required for SBF)
        indicator_block_path = None
        if indicator_block_file_id:
            try:
                indicator_file = File.objects.get(id=indicator_block_file_id)
                indicator_block_path = indicator_file.file.path
            except File.DoesNotExist:
                return {"success": False, "error": "Indicator block file not found"}
        
        # Determine output directory
        from .models import Folder
        output_folder_obj = None
        
        if output_dir_id:
            try:
                output_folder_obj = Folder.objects.get(id=output_dir_id)
                output_dir = output_folder_obj.get_full_path()
            except Folder.DoesNotExist:
                return {"success": False, "error": "Output folder not found"}
        else:
            # Auto-create 'si_tool_output' folder under the buffer file's parent folder
            parent_folder = buffer_file.folder
            
            existing_folder = Folder.objects.filter(
                name="si_tool_output",
                parent=parent_folder,
                owner=buffer_file.owner
            ).first()
            
            if existing_folder:
                output_folder_obj = existing_folder
            else:
                output_folder_obj = Folder.objects.create(
                    name="si_tool_output",
                    parent=parent_folder,
                    owner=buffer_file.owner,
                    is_public=buffer_file.is_public
                )
            
            output_dir = output_folder_obj.get_full_path()
        
        os.makedirs(output_dir, exist_ok=True)
        
        logger.info(f"Processing SI Tool: treatment={treatment}, imagery={imagery}")
        logger.info(f"Output directory: {output_dir}")
        
        # Import and run the SI tool
        from .ADMA_SI_Tool import process_si_tool
        
        success, message, output_files = process_si_tool(
            treatment=treatment,
            imagery=imagery,
            si_column_name=si_column_name,
            field_column=field_column,
            buffer_sectors_path=buffer_sectors_path,
            ndre_path=ndre_path,
            csv_path=csv_path,
            indicator_block_path=indicator_block_path,
            output_dir=output_dir
        )
        
        if not success:
            logger.error(f"SI Tool failed: {message}")
            buffer_file.processing_log = (buffer_file.processing_log or "") + f"\n✗ SI Tool failed: {message}"
            buffer_file.save(update_fields=['processing_log'])
            return {"success": False, "error": message}
        
        logger.info(f"SI Tool completed: {message}")
        
        # Create File records for output files
        created_files = []
        
        for file_type, file_path in output_files.items():
            if os.path.exists(file_path):
                try:
                    media_root = settings.MEDIA_ROOT
                    if file_path.startswith(str(media_root)):
                        relative_path = os.path.relpath(file_path, media_root)
                    else:
                        relative_path = file_path
                    
                    file_name = os.path.basename(file_path)
                    file_size = os.path.getsize(file_path)
                    
                    existing_file = File.objects.filter(
                        name=file_name,
                        folder=output_folder_obj,
                        owner=buffer_file.owner
                    ).first()
                    
                    if existing_file:
                        existing_file.file_size = file_size
                        existing_file.save(update_fields=['file_size', 'updated_at'])
                        created_files.append({
                            'name': file_name,
                            'id': str(existing_file.id),
                            'updated': True
                        })
                    else:
                        new_file = File(
                            name=file_name,
                            folder=output_folder_obj,
                            owner=buffer_file.owner,
                            file_size=file_size,
                            is_public=buffer_file.is_public,
                        )
                        new_file.file.name = relative_path
                        new_file.save()
                        
                        created_files.append({
                            'name': file_name,
                            'id': str(new_file.id),
                            'updated': False
                        })
                        
                except Exception as e:
                    logger.error(f"Error creating file record for {file_path}: {e}")
        
        # Update source file processing log
        buffer_file.processing_log = (buffer_file.processing_log or "") + f"\n✓ SI Tool completed: {message}"
        buffer_file.save(update_fields=['processing_log'])
        
        result = {
            "success": True,
            "message": message,
            "created_files": created_files,
            "output_files": {k: os.path.basename(v) for k, v in output_files.items()}
        }
        
        logger.info(f"SI Tool task completed successfully: {result}")
        return result
        
    except Exception as e:
        logger.exception(f"Error in SI Tool task")
        return {"success": False, "error": str(e)}


@shared_task(bind=True)
def sync_realm5_task(self):
    """
    Sync data from Realm5 API.
    
    This task:
    1. Gets all devices from Realm5 API
    2. Creates folders for new devices under the realm5 root folder
    3. Fetches daily observations for each device
    4. Saves observations as JSON files (one per day)
    
    This task is designed to run daily via Celery Beat.
    """
    import json
    import os
    from datetime import date, timedelta
    from django.conf import settings
    from django.core.files.base import ContentFile
    from .realm5_client import Realm5Client
    
    logger.info("Starting Realm5 sync task...")
    
    # Configuration
    REALM5_API_KEY = getattr(settings, 'REALM5_API_KEY', 'U7nEMFir1hMKTucbRsqeC2joTYGXpJy2')
    SYNC_START_DATE = date(2026, 1, 1)  # Start syncing from this date
    
    results = {
        'success': True,
        'devices_found': 0,
        'folders_created': 0,
        'files_created': 0,
        'errors': []
    }
    
    try:
        # Get the realm5 root folder
        realm5_folder = Folder.objects.filter(
            name='realm5',
            parent=None,
            is_third_party=True,
            third_party_source='realm5'
        ).first()
        
        if not realm5_folder:
            error_msg = "Realm5 root folder not found. Run 'python manage.py setup_realm5' first."
            logger.error(error_msg)
            results['success'] = False
            results['errors'].append(error_msg)
            return results
        
        owner = realm5_folder.owner
        logger.info(f"Found realm5 root folder (ID: {realm5_folder.id}, Owner: {owner.username})")
        
        # Initialize Realm5 API client
        client = Realm5Client(api_key=REALM5_API_KEY)
        
        # Step 1: Get all devices
        try:
            devices = client.get_devices()
            results['devices_found'] = len(devices)
            logger.info(f"Found {len(devices)} devices from Realm5 API")
        except Exception as e:
            error_msg = f"Failed to fetch devices from Realm5 API: {e}"
            logger.error(error_msg)
            results['success'] = False
            results['errors'].append(error_msg)
            return results
        
        # Step 2: Clean up existing non-weather-station folders
        # We only want folders for weather_station devices
        weather_station_dev_euis = set()
        for device in devices:
            if device.get('device_type') == 'weather_station':
                dev_eui_hex = device.get('dev_eui_hex')
                dev_eui_numeric = device.get('dev_eui') or device.get('devEui') or device.get('id')
                dev_eui = str(dev_eui_hex) if dev_eui_hex else str(dev_eui_numeric)
                weather_station_dev_euis.add(dev_eui)
        
        # Find and remove folders for non-weather-station devices
        existing_folders = Folder.objects.filter(parent=realm5_folder, owner=owner)
        for folder in existing_folders:
            if folder.name not in weather_station_dev_euis:
                logger.info(f"Removing folder for non-weather-station device: {folder.name}")
                folder.delete()
        
        # Step 3: Process only weather station devices
        for device in devices:
            device_type = device.get('device_type', 'unknown')
            
            # Skip non-weather-station devices - we only sync weather stations
            if device_type != 'weather_station':
                logger.debug(f"Skipping non-weather-station device: {device.get('dev_eui_hex')} (type: {device_type})")
                continue
            
            # Get both hex and numeric dev_eui
            # - Use hex EUI for folder names (more readable)
            # - Use numeric EUI for API calls (required by observations endpoint)
            dev_eui_hex = device.get('dev_eui_hex')
            dev_eui_numeric = device.get('dev_eui') or device.get('devEui') or device.get('id')
            
            if not dev_eui_numeric:
                logger.warning(f"Device missing dev_eui: {device}")
                continue
            
            # Use hex for folder name if available, otherwise numeric
            dev_eui = str(dev_eui_hex) if dev_eui_hex else str(dev_eui_numeric)
            dev_eui_for_api = str(dev_eui_numeric)  # API always needs numeric
            device_name = device.get('friendly_name') or device.get('name') or dev_eui
            
            logger.info(f"Processing weather station: {dev_eui} ({device_name})")
            
            # Check if folder exists for this device
            device_folder = Folder.objects.filter(
                name=dev_eui,
                parent=realm5_folder,
                owner=owner
            ).first()
            
            if not device_folder:
                # Create new folder for this weather station
                device_folder = Folder.objects.create(
                    name=dev_eui,
                    parent=realm5_folder,
                    owner=owner,
                    is_public=True,  # Inherit public status from parent
                    is_third_party=True,
                    third_party_source='realm5',
                    third_party_id=dev_eui,
                )
                results['folders_created'] += 1
                logger.info(f"Created folder for weather station: {dev_eui}")
            
            try:
                # Iterate from SYNC_START_DATE to yesterday, fetching observations day by day
                # We sync up to yesterday (not today) because the sync runs at 2 AM daily,
                # and today's data is not yet complete
                today = date.today()
                yesterday = today - timedelta(days=1)
                current_date = SYNC_START_DATE
                
                logger.info(f"Syncing observations for weather station: {dev_eui} from {SYNC_START_DATE} to {yesterday}")
                
                while current_date <= yesterday:
                    file_name = f"{dev_eui}_{current_date.isoformat()}.json"
                    
                    # Check if we already have a file for this date
                    existing_file = File.objects.filter(
                        name=file_name,
                        folder=device_folder,
                        owner=owner
                    ).exists()
                    
                    if existing_file:
                        logger.debug(f"File already exists, skipping: {file_name}")
                        current_date += timedelta(days=1)
                        continue
                    
                    # Fetch observations for this specific day using occurred_after/occurred_before
                    try:
                        observations = client.get_observations_by_day(dev_eui_for_api, current_date)
                        
                        if observations:
                            # Convert dict to list with timestamps
                            observations_list = [
                                {'timestamp': ts, **obs_data}
                                for ts, obs_data in observations.items()
                            ]
                            # Sort by timestamp
                            observations_list.sort(key=lambda x: x['timestamp'])
                            
                            # Create JSON file with observations
                            observation_data = {
                                'dev_eui': dev_eui,
                                'dev_eui_numeric': dev_eui_for_api,
                                'device_name': device_name,
                                'device_type': device_type,
                                'date': current_date.isoformat(),
                                'observation_count': len(observations_list),
                                'observations': observations_list,
                                'fetched_at': date.today().isoformat(),
                            }
                            
                            json_content = json.dumps(observation_data, indent=2, default=str)
                            
                            # Create file record
                            file_obj = File(
                                name=file_name,
                                folder=device_folder,
                                owner=owner,
                                file_type='text',
                                mime_type='application/json',
                                is_public=True,
                                is_third_party=True,
                                third_party_source='realm5',
                                third_party_id=f"{dev_eui}_{current_date.isoformat()}",
                            )
                            
                            # Save the file content
                            file_obj.file.save(file_name, ContentFile(json_content.encode('utf-8')))
                            file_obj.file_size = len(json_content)
                            file_obj.save()
                            
                            results['files_created'] += 1
                            logger.info(f"Created observation file: {file_name} ({len(observations_list)} observations)")
                        else:
                            logger.debug(f"No observations for {dev_eui} on {current_date}")
                            
                    except Exception as e:
                        logger.warning(f"Failed to fetch observations for {dev_eui} on {current_date}: {e}")
                        # Continue to next day even if one day fails
                    
                    current_date += timedelta(days=1)
                    
            except Exception as e:
                error_msg = f"Error processing observations for device {dev_eui}: {e}"
                logger.error(error_msg)
                results['errors'].append(error_msg)
        
        logger.info(f"Realm5 sync completed: {results}")
        return results
        
    except Exception as e:
        logger.exception("Error in sync_realm5_task")
        results['success'] = False
        results['errors'].append(str(e))
        return results


@shared_task
def sync_realm5_scheduled():
    """
    Wrapper task for scheduled Realm5 sync.
    This is called by Celery Beat on a daily schedule.
    """
    logger.info("Running scheduled Realm5 sync...")
    return sync_realm5_task.delay()
