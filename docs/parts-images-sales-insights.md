# Imagen opcional del repuesto y panel de ventas

## Imagen del catálogo

`parts.image_url` es nullable. Los repuestos existentes y los nuevos pueden guardarse sin imagen. La URL únicamente se escribe desde las rutas de carga/eliminación, no desde el JSON de edición del catálogo.

- `POST /api/v1/parts/{part_id}/image`: multipart con campo `image`; JPG, PNG o WebP, hasta 5 MB. Comprueba MIME y cabecera antes de almacenar. Devuelve `PartRead` actualizado.
- `DELETE /api/v1/parts/{part_id}/image`: quita la referencia y devuelve `PartRead`. Idempotente.
- Ambas operaciones exigen `repuestos:editar` en la filial del repuesto. Los listados incluyen `image_url` para lectores autorizados.

Se reutiliza el almacenamiento local/S3 de la aplicación. Cambiar o quitar la imagen actualiza la referencia del catálogo; los archivos antiguos se conservan, siguiendo el almacenamiento de anexos existente.

El detalle usa el modal centrado compartido, con encabezado y acciones fijas. El campo está marcado **Opcional**, presenta una vista previa y permite agregar, cambiar o quitar la imagen. También se ofrece al crear y editar. Si el guardado de datos tiene éxito y falla después la imagen, se informa que el repuesto quedó guardado y se puede reintentar desde el detalle, evitando crear duplicados.

## Indicadores comerciales

La pestaña de repuestos muestra ventas brutas, ticket promedio, unidades vendidas y clientes compradores. Incluye top 5 por unidades y top 5 de clientes por importe, con número de compras y distintivo de recurrencia. La pestaña de vehículos muestra métricas y ranking por modelo.

Los cálculos usan los listados autorizados existentes, después de aplicar búsqueda y fechas. No hay un endpoint de analítica adicional ni se amplían permisos. Los días de filtro corresponden a Caracas. En repuestos se incluyen solo estados `completado`; se excluyen cancelaciones y solicitudes pendientes/pedidas. Las ventas brutas incluyen impuestos; los importes por producto proceden de las líneas antes de impuestos. No se descuentan devoluciones; la pantalla lo indica.

La identidad del cliente se agrupa por documento normalizado; cuando no existe documento se usa el nombre normalizado. Personas con documentos diferentes no se mezclan aunque compartan nombre.

Seleccionar un nombre en los rankings filtra el historial. Los más vendidos con stock igual o inferior al mínimo generan un aviso de reposición. Los enlaces del historial llevan a ventas reales; se retiraron las etiquetas PDF sin acción.

## Migración y validación

Aplicar `venv/bin/python -m alembic upgrade head` antes de publicar el backend. Nueva revisión: `c8e3147f2a90` (padre `b5d10c73a982`). La migración se verificó con upgrade, downgrade y upgrade en PostgreSQL desechable.

Las pruebas cubren carga, reemplazo y borrado de imagen; archivos inválidos y grandes; permisos de lectura y límites de filial; exclusión de ventas canceladas/pendientes; agrupación de clientes y modelos; cálculos vacíos y stock. Chromium verificó los flujos de creación con y sin foto, errores/reintentos, modal centrado, filtros de Caracas y presentación móvil.

La suite general también tenía expectativas de garantía basadas en el día UTC y una fixture de conciliación situada en el límite del día. Se ajustaron esas pruebas al día comercial de Caracas, sin cambiar la lógica productiva correspondiente.
