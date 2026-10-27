# Vaciar las ODS de todos los concesionarios en EC2

Operación de mantenimiento manual, fuera de la API. No necesita reconstruir la imagen: copiar el script al contenedor actual. Ejecutar sin usuarios creando o modificando ODS.

## Alcance

Borra todas las ODS, tareas, ODT de taller y sus líneas/asignaciones, upsells, facturas/cobros idempotentes, garantías del taller e inspecciones y reclamos vinculados. Las inspecciones/reclamos independientes permanecen.

Conserva clientes, vehículos, catálogos, usuarios, cuentas, ingresos/egresos, existencias, lotes, movimientos de inventario y auditoría. Desvincula los ingresos/egresos de las ODS eliminadas sin cambiar montos ni cuentas. No devuelve al inventario los repuestos consumidos. Cancela los traslados pendientes de esas ODS y conserva los completados y los ajenos. Las referencias anuladas por claves foráneas están incluidas en el respaldo.

No usa `TRUNCATE CASCADE`, porque podría vaciar inventario y otras tablas ajenas al alcance.

## Ejecución

1. Subir `scripts/purge_service_orders.py` al directorio `~/bayline-deploy` de la EC2.
2. Copiarlo al contenedor y consultar la vista previa:

```bash
docker cp purge_service_orders.py app-backend-1:/tmp/purge_service_orders.py
docker exec -e PYTHONPATH=/app app-backend-1 python /tmp/purge_service_orders.py
```

3. Ejecutar el borrado con un respaldo nuevo en el volumen privado persistente:

```bash
docker exec -e PYTHONPATH=/app app-backend-1 python /tmp/purge_service_orders.py --apply --backup "/app/private-uploads/ods-backup-$(date -u +%Y%m%dT%H%M%SZ).json"
```

El resultado satisfactorio termina con `LIMPIEZA COMPLETADA. ODS restantes: 0.`. Cualquier error revierte las modificaciones en la base. El respaldo debe completarse antes de borrar; no se sobrescribe un archivo existente. Si no había ODS, la ejecución no genera respaldo ni modifica nada.

## Respaldo

El JSON contiene los registros borrados y los registros externos afectados por desvinculaciones/cancelaciones, en su estado previo. Permisos: 0600. Guardarlo fuera del contenedor además de en el volumen privado. No es un volcado íntegro de PostgreSQL ni incluye los archivos de evidencia: esos archivos se conservan. Para restaurar el JSON se necesita una restauración controlada respetando dependencias; el script no tiene modo de restauración automática. Para recuperación completa de instancia se puede tomar además el respaldo habitual de la base de datos.

Una segunda ejecución sin ODS es inocua. Las órdenes nuevas creadas después de la limpieza son datos nuevos y se borrarían en una ejecución posterior de `--apply`.
